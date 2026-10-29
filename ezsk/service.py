from __future__ import annotations

import os
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from tqdm import tqdm

from ezsk.client import SlskdClient
from ezsk.config import Config
from ezsk.dashboard import generate_dashboard
from ezsk.display import print_error, print_status
from ezsk.download import (
    PARALLEL_DOWNLOADS,
    SearchCancelled,
    SlskdUnavailable,
    _append_to_queue,
    _atomic_write_lines,
    _load_queue_lines,
    download_candidates,
    search_song,
)
from ezsk.normalize import query_hash
from ezsk.state import (
    RETRY_CAP,
    is_completed,
    is_exhausted,
    load_state,
    save_state,
    state_lock,
    state_txn,
    update_entry,
)


class DownloadService:
    def __init__(self, config: Config):
        self.config = config
        # An Event, not a bool, so the search poll loop can be woken by it --
        # otherwise a cancel waits out whatever search is in flight.
        self._cancel = threading.Event()
        self._running = False
        self._batch_total = 0
        self._batch_remaining = 0
        self._pending: list[str] = []
        self._lock = threading.Lock()
        self._ondemand: ThreadPoolExecutor | None = None
        self._reset_stale_entries()

    @property
    def _cancelled(self) -> bool:
        """Back-compat view of the cancel flag; web/app.py reads it directly."""
        return self._cancel.is_set()

    def ondemand_pool(self) -> ThreadPoolExecutor:
        """Worker pool for one-off runs kicked off from the web UI.

        These used to go straight onto asyncio's default executor, one thread
        per query. Now that searches queue behind a two-wide gate, that would
        park a dozen threads there for minutes and starve every other
        asyncio.to_thread call in the app -- including the one that starts a
        batch. A pool of our own keeps the waiting off the shared executor.
        """
        with self._lock:
            if self._ondemand is None:
                self._ondemand = ThreadPoolExecutor(
                    max_workers=max(2, self.config.search_concurrency + 2),
                    thread_name_prefix="ezsk-ondemand",
                )
            return self._ondemand

    def _reset_stale_entries(self) -> None:
        """Reset any searching/downloading entries left over from a killed run.

        Entries with status "downloaded" (file copied but not yet verified/indexed)
        are resolved: if the output file exists, mark completed; otherwise reset
        to queued for re-download.
        """
        with state_lock():
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
        self._cancel.set()

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
        queries = []
        with state_txn(self.config.state_file) as state:
            for _key, entry in state.items():
                if not isinstance(entry, dict):
                    continue
                if entry.get("status") in ("failed", "exhausted"):
                    entry["status"] = "queued"
                    entry.pop("attempts", None)
                    entry.pop("error", None)
                    queries.append(entry["query"])
        return queries

    def run_single(self, query: str) -> bool:
        client = SlskdClient(self.config)
        try:
            candidates = search_song(query, self.config, client, self._cancel)
        except SearchCancelled:
            return False
        if candidates is None:
            return False
        return download_candidates(query, candidates, self.config)

    def retry_one(self, query: str) -> bool:
        """Reset a single failed/exhausted entry and immediately re-download it."""
        with state_txn(self.config.state_file) as state:
            entry = state.get(query_hash(query))
            if not isinstance(entry, dict):
                return False
            entry["status"] = "queued"
            for field in ("error", "attempts"):
                entry.pop(field, None)
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

        with state_txn(self.config.state_file) as state:
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
        self._cancel.clear()
        completed = 0
        failed = 0
        skipped = 0
        failed_queries: list[str] = []
        exhausted_queries: list[str] = []

        # Clean up stale searches from previous runs
        try:
            cleanup_client = SlskdClient(self.config)
            # Nothing of ours is in flight yet, so sweep everything --
            # including searches a previous run was killed partway through.
            cleaned = cleanup_client.cleanup_searches(spare_active=False)
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
                exhausted_queries.append(query)
                skipped += 1
            else:
                to_download.append(query)
        if exhausted_queries:
            print_status(f"Skipping {len(exhausted_queries)} queries that hit the retry cap")

        with self._lock:
            self._batch_total = len(to_download)
            self._batch_remaining = len(to_download)

        queue_path = os.path.join(self.config.output_dir, "queued.txt")
        dead_path = os.path.join(self.config.output_dir, "dead.txt")

        # If nothing to download, handle exhausted queries and return early
        if not to_download:
            if exhausted_queries:
                _append_to_queue(dead_path, exhausted_queries)
            return completed, failed, skipped

        # Persist the work list so the batch's backlog shows as "queued" in the
        # UI before any search starts. Mirrors post_run(); get_status() merges
        # these lines in, and the cleanup at the end of the batch prunes them.
        _append_to_queue(queue_path, to_download)

        # get_status() only surfaces a queued.txt line when no state entry
        # exists for it, so a query left "failed" by an earlier run would keep
        # showing as failed even though this batch has taken it on. Flip those
        # back to "queued" — attempts and error are preserved, so the retry cap
        # still applies and the UI can still show why it last failed. The
        # save_state() also fans the refreshed counts out to SSE subscribers.
        with state_lock():
            state = load_state(self.config.state_file)
            requeued = 0
            for query in to_download:
                entry = state.get(query_hash(query))
                if isinstance(entry, dict) and entry.get("status") == "failed":
                    entry["status"] = "queued"
                    requeued += 1
            if requeued:
                save_state(self.config.state_file, state)

        self._running = True

        try:
            # Two stages, both concurrent: a narrow search pool feeding the
            # wider download pool. Searches are capped at two by the gate in
            # ezsk.client no matter what search_workers says -- see
            # Config.search_concurrency for why two is a ceiling rather than a
            # starting point.
            client = SlskdClient(self.config)
            pbar = tqdm(total=len(to_download), desc="Processing", unit="song", disable=self.config.verbose)

            # Everything below runs on the calling thread; the pools only ever
            # hand back finished futures, so the counters and the progress bar
            # need no locking of their own.
            def _resolve_download(future) -> None:
                nonlocal completed, failed
                query = download_futures.pop(future)
                try:
                    success = future.result()
                except Exception as e:
                    print_error(f"Download failed for {query}: {e}")
                    success = False
                pbar.update(1)
                if success:
                    completed += 1
                else:
                    failed += 1
                    failed_queries.append(query)

            def _resolve_search(future) -> None:
                """Hand a finished search to the download pool, or account for it."""
                nonlocal completed, failed
                query = search_futures.pop(future)
                with self._lock:
                    self._batch_remaining = max(0, self._batch_remaining - 1)
                try:
                    candidates = future.result()
                except SlskdUnavailable as e:
                    # slskd is gone and couldn't be restarted. Every search
                    # after this would fail in milliseconds, so stop here:
                    # the backlog stays queued for the next run instead of
                    # being written off. Not an attempt, like a cancel.
                    if queued:
                        print_error(
                            f"slskd is unreachable, stopping the run "
                            f"({len(queued)} queries left queued): {e}"
                        )
                        queued.clear()
                    pbar.update(1)
                    return
                except SearchCancelled:
                    # The user stopped the run. Not an attempt -- the entry is
                    # flipped back to "queued" by _reset_stale_entries().
                    pbar.update(1)
                    return
                except Exception as e:
                    # Previously an exception here killed the whole batch,
                    # since search_song was called inline with nothing around
                    # it. In a pool it would instead vanish into a future
                    # nobody inspected.
                    print_error(f"Search failed for {query}: {e}")
                    pbar.update(1)
                    failed += 1
                    failed_queries.append(query)
                    return

                if candidates is None:
                    pbar.update(1)
                    if is_completed(load_state(self.config.state_file), query):
                        completed += 1
                    else:
                        failed += 1
                        failed_queries.append(query)
                    return

                download_futures[
                    download_pool.submit(download_candidates, query, candidates, self.config)
                ] = query

            # Clamped once here: a configured 0 would otherwise make the
            # submit loop below never submit and never finish.
            search_workers = max(1, self.config.search_workers)

            with ThreadPoolExecutor(
                max_workers=PARALLEL_DOWNLOADS, thread_name_prefix="ezsk-download",
            ) as download_pool, ThreadPoolExecutor(
                max_workers=search_workers, thread_name_prefix="ezsk-search",
            ) as search_pool:
                search_futures: dict = {}
                download_futures: dict = {}
                queued = list(to_download)

                def _submit(query: str) -> None:
                    search_futures[
                        search_pool.submit(
                            search_song, query, self.config, client, self._cancel,
                        )
                    ] = query

                while queued or search_futures or download_futures:
                    if self._cancel.is_set():
                        # Drop the backlog. Searches already in flight raise
                        # SearchCancelled from their poll loop within a couple
                        # of seconds; downloads in flight are left to finish.
                        queued.clear()

                    # Keep the search pool fed, but no deeper than it can work:
                    # queries submitted further ahead than that would miss
                    # anything enqueue() adds in the meantime.
                    while queued and len(search_futures) < search_workers:
                        _submit(queued.pop(0))

                    for future in [f for f in search_futures if f.done()]:
                        _resolve_search(future)
                    for future in [f for f in download_futures if f.done()]:
                        _resolve_download(future)

                    # Pick up queries added mid-batch via enqueue()
                    new = self._drain_pending()
                    if new and not self._cancel.is_set():
                        state = load_state(self.config.state_file)
                        added = [
                            q for q in new
                            if not is_completed(state, q) and not is_exhausted(state, q)
                        ]
                        if added:
                            queued.extend(added)
                            to_download.extend(added)
                            with self._lock:
                                self._batch_total += len(added)
                                self._batch_remaining += len(added)
                            pbar.total = len(to_download)
                            pbar.refresh()

                    if not queued or len(search_futures) >= search_workers:
                        # Nothing more to submit right now -- block on whichever
                        # stage finishes next instead of spinning.
                        pending = list(search_futures) + list(download_futures)
                        if pending:
                            wait(pending, return_when=FIRST_COMPLETED, timeout=1)

                with self._lock:
                    self._batch_remaining = 0

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
