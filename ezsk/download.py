from __future__ import annotations

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from ezsk.client import SlskdClient
from ezsk.config import Config
from ezsk.dashboard import generate_dashboard
from ezsk.display import (
    format_candidate,
    interactive_select,
    print_error,
    print_status,
    print_verbose,
)
from ezsk.index import find_in_index, index_add
from ezsk.normalize import normalize_query, query_hash
from ezsk.output import format_output_path
from ezsk.queryvariants import clean_query, split_excludes, strip_suffix
from ezsk.search import rank_results
from ezsk.state import is_completed, is_exhausted, load_state, save_state, update_entry


MAX_RETRIES = 3
PARALLEL_DOWNLOADS = 4


def _find_downloaded_file(downloads_dir: str, username: str, remote_filename: str) -> str | None:
    # Soulseek paths use backslashes; os.path.basename won't split them on Unix
    basename = remote_filename.replace("\\", "/").split("/")[-1]
    for root, _dirs, files in os.walk(downloads_dir):
        if basename in files:
            return os.path.join(root, basename)
    return None


SEARCH_MIN_WAIT = 10   # seconds before checking result quality
SEARCH_TIMEOUT = 60    # total search timeout in seconds


def _smart_search(
    client: SlskdClient, query: str, config: Config,
) -> tuple[str, list[dict]]:
    """Search with quality-based early stopping.

    Polls search state and stops early once qualifying files are found
    (right format, bitrate, etc.). Explicitly stops the search before
    timeout to avoid slskd's bug of discarding responses on timeout.
    """
    try:
        search_id = client.start_search(query, timeout_ms=SEARCH_TIMEOUT * 1000)
    except Exception as e:
        err = str(e)
        if "409" in err or "Conflict" in err:
            print_status("slskd disconnected, reconnecting...")
            client.ensure_connected()
            client.cleanup_searches()
            search_id = client.start_search(query, timeout_ms=SEARCH_TIMEOUT * 1000)
        elif "400" in err or "Bad Request" in err:
            # slskd search history full — purge and retry once
            print_status("slskd search history full, purging...")
            client.cleanup_searches()
            search_id = client.start_search(query, timeout_ms=SEARCH_TIMEOUT * 1000)
        else:
            raise

    start = time.time()
    deadline = start + SEARCH_TIMEOUT + 10

    try:
        while time.time() < deadline:
            try:
                state = client.search_state(search_id)
            except Exception:
                break
            if state["isComplete"]:
                break

            elapsed = time.time() - start
            file_count = state.get("fileCount", 0)

            # Once we have files and waited long enough, stop early
            # (slskd won't return responses for active searches, so we
            # must stop before we can check quality)
            if elapsed >= SEARCH_MIN_WAIT and file_count > 0:
                response_count = state.get("responseCount", 0)
                print_verbose(
                    f"Stopping search after {elapsed:.0f}s "
                    f"({response_count} users, {file_count} files)",
                    config.verbose,
                )
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
            except Exception:
                break
            if responses:
                break
            time.sleep(0.2)
        return search_id, responses
    finally:
        client.delete_search(search_id)


def _save_state_and_dashboard(config: Config, state: dict, active: bool = True) -> None:
    save_state(config.state_file, state)
    generate_dashboard(config, active=active)


def search_song(query: str, config: Config, client: SlskdClient) -> list[dict] | None:
    """Search for a song and return ranked candidates, or None if not found."""
    state = load_state(config.state_file)
    if is_completed(state, query):
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
            update_entry(state, query, status="completed", local_path=existing)
            _save_state_and_dashboard(config, state)
            return None
    print_status(f"Searching: {query}")
    update_entry(state, query, status="searching")
    _save_state_and_dashboard(config, state)

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
        state = load_state(config.state_file)
        update_entry(state, query, status="searching", search_query=title_query)
        _save_state_and_dashboard(config, state)

        try:
            _search_id, responses = _smart_search(client, title_query, config)
        except Exception as e:
            print_error(f"Search failed: {e}")
            state = load_state(config.state_file)
            update_entry(state, query, status="failed", error=str(e), transient=True)
            _save_state_and_dashboard(config, state)
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
        state = load_state(config.state_file)
        update_entry(state, query, status="searching", search_query=simplified)
        _save_state_and_dashboard(config, state)

        try:
            _search_id, responses = _smart_search(client, simplified, config)
        except Exception as e:
            print_error(f"Search failed: {e}")
            state = load_state(config.state_file)
            update_entry(state, query, status="failed", error=str(e), transient=True)
            _save_state_and_dashboard(config, state)
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
                _search_id2, responses2 = _smart_search(client, normalize_query(variant), config)
                candidates = rank_results(
                    responses2, query_text, config.preferred_format,
                    config.min_bitrate, config.min_bit_depth,
                    artist_filter=artist, exclude_words=excludes,
                )
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
        state = load_state(config.state_file)
        update_entry(state, query, status="failed", error="no results")
        _save_state_and_dashboard(config, state)
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
        state = load_state(config.state_file)
        update_entry(
            state, query, status="downloading", username=username, remote_filename=filename
        )
        _save_state_and_dashboard(config, state)

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
            st = load_state(config.state_file)
            update_entry(
                st, query, status="downloading",
                username=username, remote_filename=filename,
                progress=bytes_transferred, total_size=total,
            )
            _save_state_and_dashboard(config, st)

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
            config.output_dir, config.output_pattern, query_text, filename
        )
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_src, output_path)

        # Checkpoint: file copied — survives crash without re-downloading
        state = load_state(config.state_file)
        update_entry(
            state, query, status="downloaded", username=username,
            remote_filename=filename, local_path=output_path,
        )
        _save_state_and_dashboard(config, state)

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

        state = load_state(config.state_file)
        update_entry(
            state, query, status="completed", username=username,
            remote_filename=filename, local_path=output_path,
        )
        _save_state_and_dashboard(config, state)

        # Clean up source file from slskd downloads dir
        try:
            os.remove(local_src)
        except OSError:
            pass

        source_basename = os.path.basename(filename.replace("\\", "/"))
        print_status(f"Saved: {output_path} (from: {source_basename})")
        return True

    state = load_state(config.state_file)
    update_entry(state, query, status="failed", error="all attempts failed")
    _save_state_and_dashboard(config, state)
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
    """Write lines to a file atomically via tempfile + os.replace()."""
    import tempfile as _tf

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = _tf.mkstemp(dir=Path(path).parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for line in sorted(lines):
                f.write(line + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
