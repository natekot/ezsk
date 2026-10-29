from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from ezsk.normalize import query_hash


def _make_state(tmp_path, entries: dict) -> str:
    """Write a state.json and return its path."""
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(entries))
    return str(state_path)


def test_run_batch_all_completed_stays_not_running(tmp_path):
    """When all queries are already completed, _running never becomes True."""
    from ezsk.config import Config
    from ezsk.service import DownloadService

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    query = "Test Artist - Test Song"
    h = query_hash(query)
    fake_file = tmp_path / "fake.flac"
    fake_file.write_text("fake")
    state_path = _make_state(tmp_path, {
        h: {"query": query, "status": "completed", "attempts": 1, "local_path": str(fake_file)},
    })

    config = Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(output_dir),
        state_file=state_path,
        log_file=str(tmp_path / "test.log"),
    )

    svc = DownloadService(config)

    with patch.object(svc, "_reset_stale_entries"):
        with patch("ezsk.service.SlskdClient"):
            completed, failed, skipped = svc.run_batch([query])

    assert svc._running is False
    assert skipped == 1
    assert completed == 0
    # Nothing to download: the early return must not seed queued.txt
    assert not (output_dir / "queued.txt").exists()


def test_run_batch_all_exhausted_stays_not_running(tmp_path):
    """When all queries are exhausted, _running never becomes True."""
    from ezsk.config import Config
    from ezsk.service import DownloadService

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    query = "Test Artist - Test Song"
    h = query_hash(query)
    state_path = _make_state(tmp_path, {
        h: {"query": query, "status": "failed", "attempts": 10},
    })

    config = Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(output_dir),
        state_file=state_path,
        log_file=str(tmp_path / "test.log"),
    )

    svc = DownloadService(config)

    with patch.object(svc, "_reset_stale_entries"):
        with patch("ezsk.service.SlskdClient"):
            completed, failed, skipped = svc.run_batch([query])

    assert svc._running is False
    assert skipped == 1


def test_run_batch_reports_exhausted_queries_in_one_line(tmp_path, capsys):
    """One summary line, not one per query.

    The auto-run loop passed the watch dir's 131 exhausted queries in every
    five minutes, and a "Giving up" line apiece put 117k lines in ezsk.log.
    """
    from ezsk.config import Config
    from ezsk.service import DownloadService

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    queries = [f"Artist {i} - Song {i}" for i in range(3)]
    state_path = _make_state(tmp_path, {
        query_hash(q): {"query": q, "status": "failed", "attempts": 5} for q in queries
    })

    config = Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(output_dir),
        state_file=state_path,
        log_file=str(tmp_path / "test.log"),
    )

    svc = DownloadService(config)

    with patch.object(svc, "_reset_stale_entries"):
        with patch("ezsk.service.SlskdClient"):
            svc.run_batch(queries)

    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert not any(q in line for q in queries for line in lines), lines
    assert [line for line in lines if "retry cap" in line] == [
        "  Skipping 3 queries that hit the retry cap"
    ]


def test_run_batch_empty_queries_not_running(tmp_path):
    """Empty query list returns immediately, _running stays False."""
    from ezsk.config import Config
    from ezsk.service import DownloadService

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    state_path = _make_state(tmp_path, {})

    config = Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(output_dir),
        state_file=state_path,
        log_file=str(tmp_path / "test.log"),
    )

    svc = DownloadService(config)

    with patch.object(svc, "_reset_stale_entries"):
        with patch("ezsk.service.SlskdClient"):
            completed, failed, skipped = svc.run_batch([])

    assert svc._running is False
    assert completed == 0
    assert failed == 0
    assert skipped == 0


def test_run_batch_queues_work_list_before_searching(tmp_path):
    """The whole work list lands in queued.txt before the first search runs."""
    from ezsk.config import Config
    from ezsk.service import DownloadService

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    queue_path = output_dir / "queued.txt"

    queries = ["A - One", "B - Two", "C - Three"]
    state_path = _make_state(tmp_path, {})

    config = Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(output_dir),
        state_file=state_path,
        log_file=str(tmp_path / "test.log"),
    )

    svc = DownloadService(config)

    # Snapshot queued.txt as each search fires, so we assert the queue was
    # persisted *before* any work started — not just repopulated afterwards by
    # the failed-query path. Searches run on a pool, so assert over every
    # observation rather than picking one by position.
    seen: list[set[str]] = []

    def _search(query, cfg, client, cancelled=None):
        seen.append({
            line.strip() for line in queue_path.read_text().splitlines() if line.strip()
        })
        return None

    with patch.object(svc, "_reset_stale_entries"):
        with patch("ezsk.service.SlskdClient"), \
             patch("ezsk.service.generate_dashboard"), \
             patch("ezsk.service.search_song", side_effect=_search):
            svc.run_batch(queries)

    assert len(seen) == len(queries)
    assert all(snapshot == set(queries) for snapshot in seen)


def test_run_batch_requeues_stale_failed_entries(tmp_path):
    """Queries left "failed" by an earlier run show as queued once re-batched.

    get_status() suppresses a queued.txt line when a state entry exists, so
    without this the tracks would keep reading "failed" for the whole run.
    """
    from ezsk.config import Config
    from ezsk.service import DownloadService

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    stale = "A - One"
    fresh = "B - Two"
    state_path = _make_state(tmp_path, {
        query_hash(stale): {
            "query": stale, "status": "failed", "attempts": 2, "error": "boom",
        },
    })

    config = Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(output_dir),
        state_file=state_path,
        log_file=str(tmp_path / "test.log"),
    )

    svc = DownloadService(config)

    seen: list[dict] = []

    def _search(query, cfg, client, cancelled=None):
        seen.append(svc.get_status()["counts"])
        raise RuntimeError("search blew up")

    with patch.object(svc, "_reset_stale_entries"):
        with patch("ezsk.service.SlskdClient"), \
             patch("ezsk.service.generate_dashboard"), \
             patch("ezsk.service.search_song", side_effect=_search):
            completed, failed, skipped = svc.run_batch([stale, fresh])

    # A search that raises is charged to its own query and nothing else: the
    # batch used to die on the first one, because search_song was called
    # inline with no handler around it.
    assert len(seen) == 2
    assert (completed, failed, skipped) == (0, 2, 0)
    # Both tracks are pending before any search resolves: the stale one via the
    # status flip, the never-seen one via queued.txt. The mocked search writes
    # no state, so every observation sees the same counts.
    assert seen[0]["queued"] == 2
    assert seen[0]["failed"] == 0

    # attempts/error survive so the retry cap still applies and the UI can
    # still show why it last failed.
    entry = json.loads(Path(state_path).read_text())[query_hash(stale)]
    assert entry["status"] == "queued"
    assert entry["attempts"] == 2
    assert entry["error"] == "boom"


def test_run_batch_searches_in_parallel_up_to_search_workers(tmp_path):
    """The batch's search stage runs concurrently, and no wider than configured.

    It used to search strictly one track at a time while four download
    workers sat idle waiting to be fed. Two is the ceiling, not a default to
    tune up -- see Config.search_concurrency.
    """
    import threading

    from ezsk.config import Config
    from ezsk.service import DownloadService

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    queries = [f"Artist {i} - Track {i}" for i in range(8)]
    config = Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(output_dir),
        state_file=_make_state(tmp_path, {}),
        log_file=str(tmp_path / "test.log"),
    )

    live = 0
    peak = 0
    lock = threading.Lock()

    def _search(query, cfg, client, cancelled=None):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.02)
        with lock:
            live -= 1
        return None

    svc = DownloadService(config)
    with patch.object(svc, "_reset_stale_entries"), \
         patch("ezsk.service.SlskdClient"), \
         patch("ezsk.service.generate_dashboard"), \
         patch("ezsk.service.search_song", side_effect=_search):
        completed, failed, skipped = svc.run_batch(queries)

    assert peak == config.search_workers == 2
    assert (completed, failed, skipped) == (0, len(queries), 0)


def test_run_batch_stops_submitting_searches_once_cancelled(tmp_path):
    """Cancel drops the backlog instead of searching the rest of the list."""
    import threading

    from ezsk.config import Config
    from ezsk.service import DownloadService

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    queries = [f"Artist {i} - Track {i}" for i in range(40)]
    config = Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(output_dir),
        state_file=_make_state(tmp_path, {}),
        log_file=str(tmp_path / "test.log"),
    )

    searched: list[str] = []
    lock = threading.Lock()
    svc = DownloadService(config)

    def _search(query, cfg, client, cancelled=None):
        with lock:
            searched.append(query)
            n = len(searched)
        if n >= 4:
            svc.cancel()
        return None

    with patch.object(svc, "_reset_stale_entries"), \
         patch("ezsk.service.SlskdClient"), \
         patch("ezsk.service.generate_dashboard"), \
         patch("ezsk.service.search_song", side_effect=_search):
        svc.run_batch(queries)

    # A couple more may already be in flight when the cancel lands; the rest
    # of the 40 must never be submitted.
    assert 4 <= len(searched) < len(queries)
