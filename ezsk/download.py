from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import requests

from ezsk import slskd_manager
from ezsk.client import SearchRejected, SlskdClient, search_gate
from ezsk.config import Config
from ezsk.dashboard import request_dashboard
from ezsk.display import (
    format_candidate,
    interactive_select,
    print_error,
    print_status,
    print_verbose,
)
from ezsk.index import find_in_index, index_add
from ezsk.normalize import normalize_query
from ezsk.output import format_output_path
from ezsk.paths import atomic_write_text
from ezsk.queryvariants import clean_query, split_excludes, strip_suffix
from ezsk.search import rank_results
from ezsk.state import is_completed, load_state, state_txn, update_entry
from ezsk.verify import read_artist_title


MAX_RETRIES = 3
PARALLEL_DOWNLOADS = 4


class SearchCancelled(Exception):
    """Raised out of a search when the run was cancelled mid-flight.

    Distinct from a failure so the caller doesn't record an attempt against
    the query -- the user stopped it, the network didn't.
    """


class SlskdUnavailable(SearchCancelled):
    """slskd can't be reached and couldn't be brought back.

    A SearchCancelled, because it is handled the same way: the query learned
    nothing about whether anyone has the track, so no attempt is charged and
    the entry goes back to "queued". run_batch additionally stops the run on
    it rather than failing the rest of the queue against a dead server.
    """


def _find_downloaded_file(downloads_dir: str, username: str, remote_filename: str) -> str | None:
    # Soulseek paths use backslashes; os.path.basename won't split them on Unix
    basename = remote_filename.replace("\\", "/").split("/")[-1]
    for root, _dirs, files in os.walk(downloads_dir):
        if basename in files:
            return os.path.join(root, basename)
    return None


# Search states slskd reports for a search it has accepted but not yet sent.
# It keeps only two searches in flight to the Soulseek server and queues the
# rest internally.
_QUEUED_STATES = {"requested", "none", ""}


def _is_dispatched(state: dict) -> bool:
    """Whether slskd has actually put this search on the wire yet.

    The distinction matters because _smart_search's clocks decide whether
    nobody has the track. A search still sitting in slskd's internal queue has
    produced nothing for reasons that have nothing to do with availability, so
    timing it from submission is what turns a queued search into a false miss
    -- and a false miss counts against the query's retry cap.

    Any state that isn't plainly queued counts as dispatched, as does a
    missing field, so an slskd build that reports something unexpected
    degrades to the old timing rather than stalling.
    """
    if state.get("fileCount", 0) or state.get("responseCount", 0):
        return True  # answers are arriving; it is plainly out there
    raw = str(state.get("state", "") or "").strip()
    if not raw:
        return True
    flags = {f.strip().lower() for f in raw.split(",")}
    return not flags <= _QUEUED_STATES


# Waits before re-trying a search slskd rate-limited, in seconds.
_RATE_LIMIT_BACKOFF = (2, 5, 10)


def _start_search(client: SlskdClient, query: str, timeout: int, config: Config) -> str:
    """Start a search, recovering from the errors slskd throws at us.

    Backs off on a 429 rather than giving up: slskd rate-limits its own search
    endpoint independently of the Soulseek server, and it answers 429 under a
    burst even at two concurrent searches. Treating that as "nobody has this
    track" is wrong and, worse, invisible -- the search comes back in
    milliseconds and the query is written off. The sleep deliberately holds
    the search gate, so backing off slows every worker, not just this one.

    409 (disconnected) and 400 (refused) each get one recovery attempt. A 400
    was long assumed to mean a full search history, but the one slskd has
    actually been seen to send is a refusal of the search text, which no
    purge can fix. So a 400 that comes back after the purge is raised as
    SearchRejected -- a real miss -- and a SearchRejected from start_search
    itself is passed straight through.
    """
    timeout_ms = timeout * 1000
    throttled = 0
    recovered = False
    while True:
        try:
            return client.start_search(query, timeout_ms=timeout_ms)
        except SearchRejected:
            raise
        except Exception as e:
            err = str(e)
            refused = "400" in err or "Bad Request" in err
            if ("429" in err or "Too Many Requests" in err) and throttled < len(_RATE_LIMIT_BACKOFF):
                delay = _RATE_LIMIT_BACKOFF[throttled]
                throttled += 1
                print_status(f"slskd rate-limited the search, retrying in {delay}s")
                time.sleep(delay)
                continue
            if recovered:
                if refused:
                    raise SearchRejected(err) from e
                raise
            if "409" in err or "Conflict" in err:
                print_status("slskd disconnected, reconnecting...")
                client.ensure_connected()
                client.cleanup_searches()
                recovered = True
                continue
            if refused:
                # Possibly a full search history -- purge and retry once.
                # Spares searches other workers have in flight (the default).
                print_status(f"slskd refused the search, clearing old searches and retrying once: {err}")
                client.cleanup_searches()
                recovered = True
                continue
            raise


def _smart_search(
    client: SlskdClient, query: str, config: Config,
    cancelled: "threading.Event | None" = None,
) -> tuple[str, list[dict]]:
    """Run one search against slskd, stopping as soon as the answer is clear.

    Held behind the process-wide search gate, so at most
    config.search_concurrency of these are open at once no matter how many
    callers there are.

    Two clocks. `dispatched_at` starts when slskd reports the search actually
    on the wire, and everything that judges the result hangs off it -- the
    early stop once qualifying files arrive, and the no-result bail when
    nobody answers at all. `hard_deadline` runs from submission and exists
    only so a search that never leaves slskd's queue can't pin a worker.

    Searches are stopped explicitly rather than left to time out: slskd serves
    no responses for an active search and discards them on timeout.
    """
    min_wait = config.search_min_wait
    timeout = config.search_timeout
    no_result_timeout = config.search_no_result_timeout

    search_gate.set_limit(config.search_concurrency)
    with search_gate.hold():
        if cancelled is not None and cancelled.is_set():
            raise SearchCancelled(query)

        search_id = _start_search(client, query, timeout, config)
        # Generous: with two searches in flight at a time, a queued one waits
        # at most about one turn of `timeout` before it goes out.
        hard_deadline = time.time() + timeout * 2 + 30
        dispatched_at: float | None = None
        state: dict = {}

        try:
            while time.time() < hard_deadline:
                if cancelled is not None and cancelled.is_set():
                    raise SearchCancelled(query)
                try:
                    state = client.search_state(search_id)
                except requests.ConnectionError:
                    raise
                except Exception:
                    break
                if state.get("isComplete"):
                    break

                if dispatched_at is None:
                    if not _is_dispatched(state):
                        time.sleep(2)
                        continue
                    dispatched_at = time.time()

                elapsed = time.time() - dispatched_at
                file_count = state.get("fileCount", 0)
                response_count = state.get("responseCount", 0)

                # Once we have files and waited long enough, stop early
                # (slskd won't return responses for active searches, so we
                # must stop before we can check quality)
                if elapsed >= min_wait and file_count > 0:
                    print_verbose(
                        f"Stopping search after {elapsed:.0f}s "
                        f"({response_count} users, {file_count} files)",
                        config.verbose,
                    )
                    break

                # Nobody has answered at all. Waiting out the full budget buys
                # nothing and, across three query variants, is most of the
                # wall-clock cost of a track nobody is sharing.
                if response_count == 0 and elapsed >= no_result_timeout:
                    print_verbose(
                        f"No responses after {elapsed:.0f}s, moving on",
                        config.verbose,
                    )
                    break

                if elapsed >= timeout:
                    break

                time.sleep(2)

            # Stop explicitly to preserve responses (slskd discards on timeout)
            if not state.get("isComplete", True):
                client.stop_search(search_id)

            # Poll until responses are available (slskd needs a moment after stop)
            responses = []
            for _ in range(15):
                try:
                    responses = client.get_search_responses(search_id)
                except requests.ConnectionError:
                    raise
                except Exception:
                    break
                if responses:
                    break
                time.sleep(0.2)
            return search_id, responses
        finally:
            client.delete_search(search_id)


def _resilient_search(
    client: SlskdClient, query: str, config: Config,
    cancelled: "threading.Event | None" = None,
) -> tuple[str, list[dict]]:
    """_smart_search, restarting slskd once if it has gone away.

    Raises SlskdUnavailable when slskd can't be reached even after that.
    """
    try:
        return _smart_search(client, query, config, cancelled)
    except requests.ConnectionError as e:
        if not slskd_manager.recover(config):
            raise SlskdUnavailable(str(e)) from e
    try:
        return _smart_search(client, query, config, cancelled)
    except requests.ConnectionError as e:
        raise SlskdUnavailable(str(e)) from e


def _record(config: Config, query: str, **fields) -> None:
    """Update one query's state entry, then refresh the dashboard.

    The load -> mutate -> save runs under the state lock (see state.state_txn),
    which matters now that searches and downloads both run on worker threads.
    generate_dashboard() stays outside the lock -- it re-reads state.json and
    rewrites the whole HTML page, and holding the lock across that would
    serialize the pipeline behind it.
    """
    with state_txn(config.state_file) as state:
        update_entry(state, query, **fields)
    request_dashboard(config, active=True)


def search_song(
    query: str, config: Config, client: SlskdClient,
    cancelled: "threading.Event | None" = None,
) -> list[dict] | None:
    """Search for a song and return ranked candidates, or None if not found.

    Raises SearchCancelled if `cancelled` is set while a search is in flight,
    so the caller can tell "the user stopped this" from "nobody has it".
    """
    if is_completed(load_state(config.state_file), query):
        print_status(f"Already downloaded: {query}")
        return None
    # `query` stays the raw line the user typed -- it is the state key, so state
    # entries, the dashboard and queued.txt must all keep it. `query_text` is the
    # same line with the `no:...` tokens removed, and is what gets treated as
    # content: searched for, scored against, and used to name the output file.
    query_text, excludes = split_excludes(query)
    if config.index is not None:
        existing = find_in_index(config.index, query_text)
        if existing:
            print_status(f"Found in index: {existing}")
            _record(config, query, status="completed", local_path=existing)
            return None
    print_status(f"Searching: {query}")
    _record(config, query, status="searching")

    use_ai = bool(config.anthropic_api_key)
    cleaned = clean_query(query_text)
    simplified = normalize_query(cleaned)
    parts = cleaned.split(" - ", 1)
    has_artist = len(parts) == 2
    artist = parts[0] if has_artist else ""
    title = parts[1] if has_artist else cleaned

    # Title-only search: search by title, filter by artist in ranking
    candidates = []
    if config.title_only_search and has_artist:
        title_query = normalize_query(title)
        _record(config, query, status="searching", search_query=title_query)

        try:
            _search_id, responses = _resilient_search(client, title_query, config, cancelled)
        except SearchCancelled:
            raise
        except SearchRejected as e:
            # A title like "I.D.L.E." is all one-letter words, which slskd
            # won't search on their own. The artist gives the full search
            # below something to go on.
            print_verbose(f"Title-only search refused, trying the full search: {e}", config.verbose)
            responses = []
        except Exception as e:
            print_error(f"Search failed: {e}")
            _record(config, query, status="failed", error=str(e), transient=True)
            return None

        total_files = sum(len(r.get("files", [])) for r in responses)
        print_verbose(f"Title-only search returned {len(responses)} users, {total_files} files", config.verbose)

        candidates = rank_results(
            responses, query_text, config.preferred_format, config.min_bitrate,
            config.min_bit_depth, artist_filter=artist, exclude_words=excludes,
        )
        if candidates:
            print_verbose(f"Title-only search: {len(candidates)} candidates after artist filter", config.verbose)

    # Fallback: full artist+title search (or primary if title_only_search disabled)
    if not candidates:
        _record(config, query, status="searching", search_query=simplified)

        try:
            _search_id, responses = _resilient_search(client, simplified, config, cancelled)
        except SearchCancelled:
            raise
        except SearchRejected as e:
            # slskd will never accept this text, so retrying can't help: a
            # real miss, and it counts toward the retry cap.
            print_error(f"Search failed: {e}")
            _record(config, query, status="failed", error=str(e))
            return None
        except Exception as e:
            print_error(f"Search failed: {e}")
            _record(config, query, status="failed", error=str(e), transient=True)
            return None

        total_files = sum(len(r.get("files", [])) for r in responses)
        print_verbose(f"Full search returned {len(responses)} users, {total_files} files", config.verbose)

        candidates = rank_results(
            responses, query_text, config.preferred_format, config.min_bitrate,
            config.min_bit_depth, artist_filter=artist, exclude_words=excludes,
        )

    # Fallback: try with remix/version suffixes stripped
    if not candidates:
        variant = strip_suffix(cleaned)
        if variant:
            print_verbose(f"Retrying without suffix: {variant}", config.verbose)
            try:
                _search_id2, responses2 = _resilient_search(
                    client, normalize_query(variant), config, cancelled
                )
                candidates = rank_results(
                    responses2, query_text, config.preferred_format,
                    config.min_bitrate, config.min_bit_depth,
                    artist_filter=artist, exclude_words=excludes,
                )
            except SearchCancelled:
                raise
            except Exception:
                pass

    # AI-powered result filtering
    if candidates and use_ai:
        from ezsk.ai_match import match_results
        try:
            filenames = [c["filename"] for c in candidates[:20]]  # cap to control cost
            matching = match_results(query_text, filenames, config.anthropic_api_key)
            if matching:
                candidates = [candidates[i] for i in matching]
                print_verbose(f"AI matched {len(candidates)} of {len(filenames)} candidates", config.verbose)
            else:
                print_verbose("AI found no matches, using top ranked", config.verbose)
        except Exception as e:
            print_verbose(f"AI match failed ({e}), using ranked results", config.verbose)

    if not candidates:
        print_error(f"No results for: {query}")
        _record(config, query, status="failed", error="no results")
        return None

    print_verbose(f"Found {len(candidates)} candidates", config.verbose)

    if config.interactive:
        selected = interactive_select(candidates)
        if selected is None:
            print_status("Skipped.")
            return None
        return [selected]

    if config.verbose and candidates:
        print(format_candidate(candidates[0], 0))
    return candidates[:MAX_RETRIES]


def download_candidates(query: str, candidates: list[dict], config: Config) -> bool:
    """Download a song from a list of ranked candidates. Returns True on success."""
    client = SlskdClient(config)
    # See search_song: raw `query` keys the state, `query_text` names the file
    # and is what the downloaded tags are checked against.
    query_text, _ = split_excludes(query)

    for i, candidate in enumerate(candidates):
        username = candidate["username"]
        filename = candidate["filename"]
        size = candidate["size"]

        print_verbose(
            f"Attempt {i + 1}/{len(candidates)}: {os.path.basename(filename)} from {username}",
            config.verbose,
        )
        _record(
            config, query, status="downloading", username=username,
            remote_filename=filename,
        )

        try:
            client.enqueue_download(username, filename, size)
        except Exception as e:
            print_verbose(f"Enqueue failed: {e}", config.verbose)
            continue

        print_status(f"Downloading from {username}...")
        last_dashboard_update = [0.0]

        def _on_progress(bytes_transferred: int, total: int) -> None:
            now = time.time()
            if now - last_dashboard_update[0] < 1:
                return
            last_dashboard_update[0] = now
            _record(
                config, query, status="downloading",
                username=username, remote_filename=filename,
                progress=bytes_transferred, total_size=total,
            )

        result = client.wait_for_download(
            username, filename, timeout=config.timeout, on_progress=_on_progress,
        )

        if result is None:
            print_verbose("Download failed or timed out", config.verbose)
            continue

        # Copy file from slskd downloads dir to output dir
        local_src = _find_downloaded_file(config.downloads_dir, username, filename)
        if local_src is None:
            print_error("Downloaded file not found in slskd downloads directory")
            continue

        output_path = format_output_path(
            config.output_dir, config.output_pattern, query_text, filename,
            tags=read_artist_title(local_src),
        )
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_src, output_path)

        # Checkpoint: file copied — survives crash without re-downloading
        _record(
            config, query, status="downloaded", username=username,
            remote_filename=filename, local_path=output_path,
        )

        if config.verify:
            from ezsk.verify import verify_file

            result = verify_file(
                output_path, config.min_bitrate, config.min_bit_depth,
                config.min_frequency_cutoff,
            )
            if not result.passed:
                print_error(
                    f"Rejected: {os.path.basename(filename)} — {result.reason}"
                )
                os.remove(output_path)
                continue

        if config.strict_metadata_check:
            from ezsk.verify import check_metadata_match

            meta_error = check_metadata_match(output_path, query_text)
            if meta_error:
                print_error(f"Rejected: {os.path.basename(filename)} — {meta_error}")
                os.remove(output_path)
                continue

        if config.index is not None:
            index_add(config.index, config.output_dir, query_text, output_path)

        _record(
            config, query, status="completed", username=username,
            remote_filename=filename, local_path=output_path,
        )

        # Clean up source file from slskd downloads dir
        try:
            os.remove(local_src)
        except OSError:
            pass

        source_basename = os.path.basename(filename.replace("\\", "/"))
        print_status(f"Saved: {output_path} (from: {source_basename})")
        return True

    _record(config, query, status="failed", error="all attempts failed")
    print_error(f"Failed after {len(candidates)} attempts: {query}")
    return False


def download_song(query: str, config: Config) -> bool:
    """Search and download a single song. Used for single-query mode."""
    from ezsk.service import DownloadService
    return DownloadService(config).run_single(query)


def _load_queue_lines(queue_path: str) -> set[str]:
    if not os.path.exists(queue_path):
        return set()
    with open(queue_path) as f:
        return {line.strip() for line in f if line.strip()}


def _atomic_write_lines(path: str, lines: set[str] | list[str]) -> None:
    """Write lines to a file atomically."""
    atomic_write_text(path, "".join(line + "\n" for line in sorted(lines)))


def _append_to_queue(queue_path: str, misses: list[str]) -> None:
    existing = _load_queue_lines(queue_path)
    new_lines = [q for q in misses if q not in existing]
    if not new_lines:
        return
    _atomic_write_lines(queue_path, existing | set(new_lines))


def download_batch(
    queries: list[str], config: Config, *, input_file: str | None = None,
) -> tuple[int, int, int]:
    """Batch download. Delegates to DownloadService."""
    from ezsk.service import DownloadService
    return DownloadService(config).run_batch(queries, input_file=input_file)
