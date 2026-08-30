from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from ezsk.client import SlskdClient
from ezsk.config import Config
from ezsk.dashboard import generate_dashboard
from ezsk.display import print_error, print_status
from ezsk.download import (
    PARALLEL_DOWNLOADS,
    _append_to_queue,
    _atomic_write_lines,
    _load_queue_lines,
    download_candidates,
    search_song,
)
from ezsk.normalize import query_hash
from ezsk.state import RETRY_CAP, is_completed, is_exhausted, load_state, save_state, update_entry


class DownloadService:
    def __init__(self, config: Config):
        self.config = config
        self._cancelled = False
        self._running = False
        self._batch_total = 0
        self._batch_remaining = 0
        self._pending: list[str] = []
        self._lock = threading.Lock()
        self._reset_stale_entries()

    def _reset_stale_entries(self) -> None:
        """Reset any searching/downloading entries left over from a killed run.

        Entries with status "downloaded" (file copied but not yet verified/indexed)
        are resolved: if the output file exists, mark completed; otherwise reset
        to queued for re-download.
        """
        state = load_state(self.config.state_file)
        changed = 0
        for entry in state.values():
            if not isinstance(entry, dict):
                continue
            status = entry.get("status")
            if status in ("searching", "downloading"):
                entry["status"] = "queued"
                changed += 1
            elif status == "downloaded":
                local_path = entry.get("local_path", "")
                if local_path and os.path.exists(local_path):
                    entry["status"] = "completed"
                else:
                    entry["status"] = "queued"
                changed += 1
        if changed:
            save_state(self.config.state_file, state)

    @property
    def running(self) -> bool:
        return self._running

    def cancel(self) -> None:
        self._cancelled = True

    def enqueue(self, queries: list[str]) -> None:
        """Add queries to be picked up by the running batch."""
        with self._lock:
            self._pending.extend(queries)

    def get_status(self) -> dict:
        state = load_state(self.config.state_file)
        counts = {"completed": 0, "failed": 0, "searching": 0, "downloading": 0, "queued": 0, "dead": 0}
        entries = []
        for _key, entry in state.items():
            if not isinstance(entry, dict):
                continue
            status = entry.get("status", "unknown")
            # A "queued" entry that has hit the retry cap is effectively dead —
            # run_batch would skip it. Reclassify so it shows in the right bucket.
            if status == "queued" and entry.get("attempts", 0) >= RETRY_CAP:
                status = "dead"
                entry = {**entry, "status": "dead"}
            if status in counts:
                counts[status] += 1
            entries.append(entry)

        queue_path = os.path.join(self.config.output_dir, "queued.txt")
        dead_path = os.path.join(self.config.output_dir, "dead.txt")
        state_hashes = {query_hash(e.get("query", "")) for e in entries}
        for txt_path, txt_status in ((queue_path, "queued"), (dead_path, "dead")):
            for query in _load_queue_lines(txt_path):
                if query_hash(query) not in state_hashes:
                    counts[txt_status] += 1
                    entries.append({"query": query, "status": txt_status})

        with self._lock:
            batch_total = self._batch_total
            batch_remaining = self._batch_remaining

        return {
            "counts": counts,
            "entries": entries,
            "batch_total": batch_total,
            "batch_remaining": batch_remaining,
        }

    def retry_failed(self) -> list[str]:
        state = load_state(self.config.state_file)
        queries = []
        for _key, entry in state.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("status") in ("failed", "exhausted"):
                entry["status"] = "queued"
                entry.pop("attempts", None)
                entry.pop("error", None)
                queries.append(entry["query"])
        save_state(self.config.state_file, state)
        return queries

    def run_single(self, query: str) -> bool:
        client = SlskdClient(self.config)
        candidates = search_song(query, self.config, client)
        if candidates is None:
            return False
        return download_candidates(query, candidates, self.config)

    def retry_one(self, query: str) -> bool:
        """Reset a single failed/exhausted entry and immediately re-download it."""
        state = load_state(self.config.state_file)
        entry = state.get(query_hash(query))
        if not isinstance(entry, dict):
            return False
        entry["status"] = "queued"
        for field in ("error", "attempts"):
            entry.pop(field, None)
        save_state(self.config.state_file, state)
        return self.run_single(query)

    def replace(self, query: str, new_query: str | None = None) -> bool:
        """Reject a completed track and re-download, optionally under a new query.

        Deletes the current file and drops it from the library index. If
        `new_query` matches `query`, the existing entry is reset to "queued" in
        place. If it differs (the user edited the search string), the old entry is
        removed and a fresh "queued" entry is created for the new query so the
        original isn't left as a phantom. Then re-runs the normal search/download
        pipeline. Returns the re-download result (False if there was nothing to
        replace).
        """
        from ezsk.index import index_remove

        target = (new_query or query).strip() or query

        state = load_state(self.config.state_file)
        entry = state.get(query_hash(query))
        if not isinstance(entry, dict):
            return False

        local_path = entry.get("local_path", "")
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except OSError:
                pass

        if self.config.index is not None:
            index_remove(self.config.index, self.config.output_dir, query)

        if query_hash(target) == query_hash(query):
            # Same query: reset the existing entry in place.
            entry["status"] = "queued"
            for field in ("local_path", "error", "attempts", "progress", "total_size"):
                entry.pop(field, None)
        else:
            # Superseded by an edited query: drop the old entry, queue the new one.
            state.pop(query_hash(query), None)
            update_entry(state, target, status="queued")
        save_state(self.config.state_file, state)

        return self.run_single(target)

    def _drain_pending(self) -> list[str]:
        """Collect and clear any queries added via enqueue()."""
        with self._lock:
            pending = list(self._pending)
            self._pending.clear()
        return pending

    def run_batch(
        self, queries: list[str], *, input_file: str | None = None,
    ) -> tuple[int, int, int]:
        self._cancelled = False
        completed = 0
        failed = 0
        skipped = 0
        failed_queries: list[str] = []
        exhausted_queries: list[str] = []

        # Clean up stale searches from previous runs
        try:
            cleanup_client = SlskdClient(self.config)
            cleaned = cleanup_client.cleanup_searches()
            if cleaned:
                print_status(f"Cleaned up {cleaned} stale searches")
        except Exception as e:
            print_error(f"Cleanup failed: {e}")

        # Filter out completed/exhausted
        to_download: list[str] = []
        state = load_state(self.config.state_file)
        for query in queries:
            if is_completed(state, query):
                skipped += 1
            elif is_exhausted(state, query):
                print_status(f"Giving up after {state.get(query_hash(query), {}).get('attempts', '?')} attempts: {query}")
                exhausted_queries.append(query)
                skipped += 1
            else:
                to_download.append(query)

        with self._lock:
            self._batch_total = len(to_download)
            self._batch_remaining = len(to_download)

        # If nothing to download, handle exhausted queries and return early
        if not to_download:
            if exhausted_queries:
                dead_path = os.path.join(self.config.output_dir, "dead.txt")
                _append_to_queue(dead_path, exhausted_queries)
            return completed, failed, skipped

        self._running = True

        try:
            # Search sequentially, download in parallel as results come in
            client = SlskdClient(self.config)
            pbar = tqdm(total=len(to_download), desc="Processing", unit="song", disable=self.config.verbose)

            with ThreadPoolExecutor(max_workers=PARALLEL_DOWNLOADS) as pool:
                futures: dict = {}

                for i, query in enumerate(to_download):
                    if self._cancelled:
                        break

                    with self._lock:
                        # Tracks after this one that haven't been searched yet
                        self._batch_remaining = len(to_download) - i - 1

                    # Collect finished downloads before each search
                    done = [f for f in futures if f.done()]
                    for f in done:
                        q, success = f.result()
                        pbar.update(1)
                        if success:
                            completed += 1
                        else:
                            failed += 1
                            failed_queries.append(q)
                        del futures[f]

                    candidates = search_song(query, self.config, client)
                    if candidates is None:
                        pbar.update(1)
                        if is_completed(load_state(self.config.state_file), query):
                            completed += 1
                        else:
                            failed += 1
                            failed_queries.append(query)
                    else:
                        def _do_download(q=query, c=candidates):
                            return q, download_candidates(q, c, self.config)
                        future = pool.submit(_do_download)
                        futures[future] = query

                    # Pick up queries added mid-batch via enqueue()
                    new = self._drain_pending()
                    if new:
                        state = load_state(self.config.state_file)
                        for nq in new:
                            if not is_completed(state, nq) and not is_exhausted(state, nq):
                                to_download.append(nq)
                        pbar.total = len(to_download)
                        pbar.refresh()

                # Wait for remaining downloads
                with self._lock:
                    self._batch_remaining = 0
                for future in as_completed(futures):
                    q, success = future.result()
                    pbar.update(1)
                    if success:
                        completed += 1
                    else:
                        failed += 1
                        failed_queries.append(q)

            pbar.close()
        finally:
            # Always clean up stale in-flight entries and counters,
            # even if the batch crashes mid-run
            self._reset_stale_entries()
            with self._lock:
                self._batch_total = 0
                self._batch_remaining = 0

        # Final dashboard without active flag
        generate_dashboard(self.config)

        # Queue management
        queue_path = os.path.join(self.config.output_dir, "queued.txt")
        dead_path = os.path.join(self.config.output_dir, "dead.txt")

        if failed_queries:
            _append_to_queue(queue_path, failed_queries)
            print_status(f"{len(failed_queries)} misses queued in {queue_path}")

        if exhausted_queries:
            _append_to_queue(dead_path, exhausted_queries)
            print_status(f"{len(exhausted_queries)} queries permanently failed → {dead_path}")

        # Clean queued.txt: remove completed/exhausted entries
        state = load_state(self.config.state_file)
        done_hashes = {
            k for k, v in state.items()
            if isinstance(v, dict) and v.get("status") in ("completed", "exhausted")
        }
        for txt_path in (queue_path, dead_path):
            existing = _load_queue_lines(txt_path)
            if not existing:
                continue
            remaining = {q for q in existing if query_hash(q) not in done_hashes and not is_exhausted(state, q)}
            if len(remaining) < len(existing):
                _atomic_write_lines(txt_path, remaining)

        self._running = False
        return completed, failed, skipped
