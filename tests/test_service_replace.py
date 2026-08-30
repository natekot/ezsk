from __future__ import annotations

import json
import os
from unittest.mock import patch

from ezsk.normalize import query_hash


def _make_config(tmp_path, state_path, index=None):
    from ezsk.config import Config

    output_dir = tmp_path / "output"
    output_dir.mkdir(exist_ok=True)
    return Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(output_dir),
        state_file=str(state_path),
        log_file=str(tmp_path / "test.log"),
        index=index,
    )


def _service(config):
    from ezsk.service import DownloadService

    with patch.object(DownloadService, "_reset_stale_entries"):
        return DownloadService(config)


def test_replace_deletes_file_resets_entry_and_redownloads(tmp_path):
    from ezsk.index import index_add, load_index

    query = "Some Artist - Some Song"
    h = query_hash(query)

    state_path = tmp_path / "state.json"
    # The completed file that the user deems wrong/corrupt.
    bad_file = tmp_path / "output" / "Some Artist - Some Song.flac"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("corrupt")
    state_path.write_text(json.dumps({
        h: {
            "query": query, "status": "completed", "attempts": 2,
            "local_path": str(bad_file), "username": "peer1",
            "remote_filename": "music\\bad.flac", "error": "",
        },
    }))

    index = {}
    config = _make_config(tmp_path, state_path, index=index)
    index_add(index, config.output_dir, query, str(bad_file))

    svc = _service(config)

    with patch.object(svc, "run_single", return_value=True) as run_single:
        result = svc.replace(query)

    assert result is True
    run_single.assert_called_once_with(query)

    # The bad file is gone.
    assert not bad_file.exists()

    # Index no longer references the supplanted track.
    assert not load_index(config.output_dir)

    # State entry reset to queued with stale fields cleared.
    state = json.loads(state_path.read_text())[h]
    assert state["status"] == "queued"
    assert "local_path" not in state
    assert "attempts" not in state
    assert "error" not in state


def test_replace_with_edited_query_supersedes_old_entry(tmp_path):
    from ezsk.index import index_add, load_index

    query = "Some Artist - Some Song"
    new_query = "Some Artist - Some Song (Remastered)"
    h = query_hash(query)
    nh = query_hash(new_query)

    state_path = tmp_path / "state.json"
    bad_file = tmp_path / "output" / "Some Artist - Some Song.flac"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("wrong version")
    state_path.write_text(json.dumps({
        h: {"query": query, "status": "completed", "attempts": 1,
            "local_path": str(bad_file)},
    }))

    index = {}
    config = _make_config(tmp_path, state_path, index=index)
    index_add(index, config.output_dir, query, str(bad_file))

    svc = _service(config)

    with patch.object(svc, "run_single", return_value=True) as run_single:
        result = svc.replace(query, new_query)

    assert result is True
    # Re-download runs under the edited query, not the original.
    run_single.assert_called_once_with(new_query)
    assert not bad_file.exists()
    assert not load_index(config.output_dir)

    state = json.loads(state_path.read_text())
    # Old entry is gone; a fresh queued entry exists under the new query's hash.
    assert h not in state
    assert state[nh]["status"] == "queued"
    assert state[nh]["query"] == new_query


def test_replace_unknown_query_is_noop(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({}))
    config = _make_config(tmp_path, state_path)
    svc = _service(config)

    with patch.object(svc, "run_single") as run_single:
        assert svc.replace("Nobody - Nothing") is False
    run_single.assert_not_called()


def test_replace_tolerates_already_missing_file(tmp_path):
    query = "Gone Artist - Gone Song"
    h = query_hash(query)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        h: {"query": query, "status": "completed",
            "local_path": str(tmp_path / "output" / "missing.flac")},
    }))
    config = _make_config(tmp_path, state_path)
    svc = _service(config)

    with patch.object(svc, "run_single", return_value=False) as run_single:
        assert svc.replace(query) is False
    run_single.assert_called_once_with(query)

    state = json.loads(state_path.read_text())[h]
    assert state["status"] == "queued"
