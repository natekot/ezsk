from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def app_state(tmp_path, monkeypatch):
    """A bare AppState with no background update checker."""
    from ezsk.config import Config
    import ezsk.paths as paths_mod

    monkeypatch.setattr(paths_mod, "_migrated", True)
    monkeypatch.setattr(paths_mod, "user_config_dir", lambda *a, **kw: str(tmp_path / "config"))
    monkeypatch.setattr(paths_mod, "user_data_dir", lambda *a, **kw: str(tmp_path / "data"))
    for name in ("config", "data", "output", "dl", "watch"):
        (tmp_path / name).mkdir(exist_ok=True)

    config = Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(tmp_path / "output"),
        state_file=str(tmp_path / "state.json"),
        log_file=str(tmp_path / "test.log"),
    )
    config.watch_dir = str(tmp_path / "watch")
    config.anthropic_api_key = "test-anthropic-key"

    with patch("ezsk.updater.UpdateChecker") as MockUC:
        MockUC.return_value = MagicMock()
        from ezsk.web.app import AppState
        yield AppState(config), config


def test_files_payload_reports_configured_watch_dir(app_state):
    """The push payload carries the watch dir so the UI can tell it is set up."""
    state, config = app_state

    payload = state.files_payload()

    assert payload["watch_dir"] == config.watch_dir
    assert payload["files"] == []


def test_files_payload_reports_missing_watch_dir(app_state):
    """With no watch dir configured the payload says so explicitly."""
    state, config = app_state
    state.config.watch_dir = ""

    payload = state.files_payload()

    assert payload["watch_dir"] == ""
    assert payload["files"] == []


def test_files_payload_lists_watch_dir_contents(app_state):
    """Files present in the watch dir show up in the payload."""
    import os
    state, config = app_state
    with open(os.path.join(config.watch_dir, "playlist.csv"), "w") as f:
        f.write("Artist - Track\n")

    payload = state.files_payload()

    assert [f["name"] for f in payload["files"]] == ["playlist.csv"]
