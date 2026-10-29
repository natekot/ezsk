from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def upload_client(tmp_path, monkeypatch):
    """Test client with a configured watch dir.

    AI parsing and the download service are stubbed so uploading never
    reaches Anthropic or slskd.
    """
    from starlette.testclient import TestClient
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

    with patch("ezsk.updater.UpdateChecker") as MockUC, \
            patch("ezsk.watch.parse_file_with_ai", return_value=["Artist - Track"]), \
            patch("ezsk.service.DownloadService.run_batch", return_value=None):
        mock_uc = MagicMock()
        mock_uc.to_dict.return_value = {"checked": False}
        MockUC.return_value = mock_uc

        from ezsk.web.app import create_app
        app = create_app(config)
        with TestClient(app) as client:
            yield client, tmp_path / "watch"


def test_upload_saves_every_file(upload_client):
    """All files sent under the repeated 'files' field are saved, not just the last."""
    client, watch_dir = upload_client

    response = client.post("/api/files/upload", files=[
        ("files", ("first.csv", b"one\n", "text/csv")),
        ("files", ("second.csv", b"two\n", "text/csv")),
        ("files", ("third.csv", b"three\n", "text/csv")),
    ])

    assert response.status_code == 200
    assert sorted(response.json()["uploaded"]) == ["first.csv", "second.csv", "third.csv"]
    assert (watch_dir / "first.csv").read_bytes() == b"one\n"
    assert (watch_dir / "second.csv").read_bytes() == b"two\n"
    assert (watch_dir / "third.csv").read_bytes() == b"three\n"


def test_upload_saves_single_file(upload_client):
    """A single uploaded file lands in the watch dir."""
    client, watch_dir = upload_client

    response = client.post(
        "/api/files/upload",
        files={"files": ("only.csv", b"just one\n", "text/csv")},
    )

    assert response.status_code == 200
    assert response.json()["uploaded"] == ["only.csv"]
    assert (watch_dir / "only.csv").read_bytes() == b"just one\n"
