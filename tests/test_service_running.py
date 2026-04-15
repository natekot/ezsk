from __future__ import annotations

import json
import os
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
