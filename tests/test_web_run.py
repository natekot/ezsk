"""POST /api/run says which queries it skipped as already downloaded.

Seen live: re-adding "tapper zukie visions of love" -- saved weeks earlier as
"Unknown - tapper zukie visions of love.flac" -- got back {"count": 0}, which
the page shows as "Started 0 queries": no hint the track was already there.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from ezsk.normalize import query_hash


@pytest.fixture
def run_client(tmp_path, monkeypatch):
    """Test client with no watch dir, so the auto-run loop stays off."""
    from starlette.testclient import TestClient
    from ezsk.config import Config
    import ezsk.paths as paths_mod

    monkeypatch.setattr(paths_mod, "_migrated", True)
    monkeypatch.setattr(paths_mod, "user_config_dir", lambda *a, **kw: str(tmp_path / "config"))
    monkeypatch.setattr(paths_mod, "user_data_dir", lambda *a, **kw: str(tmp_path / "data"))
    for name in ("config", "data", "output", "dl"):
        (tmp_path / name).mkdir(exist_ok=True)

    done = tmp_path / "output" / "Unknown - tapper zukie visions of love.flac"
    done.write_bytes(b"fLaC")
    (tmp_path / "state.json").write_text(json.dumps({
        query_hash("tapper zukie visions of love"): {
            "query": "tapper zukie visions of love", "status": "completed",
            "local_path": str(done),
        },
    }))

    config = Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(tmp_path / "output"),
        state_file=str(tmp_path / "state.json"),
        log_file=str(tmp_path / "test.log"),
    )

    with patch("ezsk.updater.UpdateChecker") as MockUC, \
            patch("ezsk.service.DownloadService.run_single", return_value=False):
        mock_uc = MagicMock()
        mock_uc.to_dict.return_value = {"checked": False}
        MockUC.return_value = mock_uc

        from ezsk.web.app import create_app
        with TestClient(create_app(config)) as client:
            yield client, str(done)


def test_an_already_downloaded_query_is_reported_with_its_file(run_client):
    client, done = run_client

    r = client.post("/api/run", json={"queries": ["tapper zukie visions of love"]})

    assert r.json() == {
        "ok": True,
        "count": 0,
        "already": [{"query": "tapper zukie visions of love", "local_path": done}],
    }


def test_new_queries_still_start_alongside_the_skipped_ones(run_client):
    client, done = run_client

    r = client.post("/api/run", json={"queries": [
        "tapper zukie visions of love", "Tin Tin Out - The Feeling",
    ]})

    body = r.json()
    assert body["count"] == 1
    assert body["already"] == [{"query": "tapper zukie visions of love", "local_path": done}]
