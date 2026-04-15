from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """Create a test client for the web app."""
    from starlette.testclient import TestClient
    from ezsk.config import Config
    import ezsk.paths as paths_mod

    # Redirect all paths to tmp
    monkeypatch.setattr(paths_mod, "_migrated", True)
    monkeypatch.setattr(paths_mod, "user_config_dir", lambda *a, **kw: str(tmp_path / "config"))
    monkeypatch.setattr(paths_mod, "user_data_dir", lambda *a, **kw: str(tmp_path / "data"))
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)

    config = Config(
        url="http://localhost:5030",
        api_key="test-key-long-enough-for-validation",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(tmp_path / "output"),
        state_file=str(tmp_path / "state.json"),
        log_file=str(tmp_path / "test.log"),
    )
    (tmp_path / "output").mkdir(exist_ok=True)
    (tmp_path / "dl").mkdir(exist_ok=True)

    # Mock the update checker to not start background thread
    with patch("ezsk.updater.UpdateChecker") as MockUC:
        mock_uc = MagicMock()
        mock_uc.to_dict.return_value = {"checked": False}
        mock_uc.start = MagicMock()
        MockUC.return_value = mock_uc

        from ezsk.web.app import create_app
        app = create_app(config)

    client = TestClient(app)
    return client, app


def test_index_returns_html(app_client):
    client, app = app_client
    resp = client.get("/")
    assert resp.status_code == 200
    assert "html" in resp.headers.get("content-type", "")


def test_index_shows_setup_when_needed(tmp_path, monkeypatch):
    """Index returns setup.html when needs_setup is True."""
    from starlette.testclient import TestClient
    from ezsk.config import Config
    import ezsk.paths as paths_mod

    monkeypatch.setattr(paths_mod, "_migrated", True)
    monkeypatch.setattr(paths_mod, "user_config_dir", lambda *a, **kw: str(tmp_path / "config"))
    monkeypatch.setattr(paths_mod, "user_data_dir", lambda *a, **kw: str(tmp_path / "data"))
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)

    # Config with NO api key → triggers needs_setup
    config = Config(
        url="http://localhost:5030",
        api_key="",
        downloads_dir=str(tmp_path / "dl"),
        output_dir=str(tmp_path / "output"),
        state_file=str(tmp_path / "state.json"),
        log_file=str(tmp_path / "test.log"),
    )
    (tmp_path / "output").mkdir(exist_ok=True)
    (tmp_path / "dl").mkdir(exist_ok=True)

    with patch("ezsk.updater.UpdateChecker") as MockUC:
        mock_uc = MagicMock()
        mock_uc.to_dict.return_value = {"checked": False}
        mock_uc.start = MagicMock()
        MockUC.return_value = mock_uc

        from ezsk.web.app import create_app
        app = create_app(config)

    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "setup" in resp.text.lower()


def test_get_status(app_client):
    client, app = app_client
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "counts" in data
    assert "running" in data


def test_get_update(app_client):
    client, app = app_client
    resp = client.get("/api/update")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"checked": False}


def test_setup_external_missing_api_key(app_client):
    client, app = app_client
    resp = client.post("/api/setup", json={
        "mode": "external",
        "url": "http://localhost:5030",
        "api_key": "",
        "downloads_dir": "/tmp/dl",
    })
    assert resp.status_code == 400
    assert "API key" in resp.json().get("error", "")


def test_setup_status_before_setup(app_client):
    client, app = app_client
    resp = client.get("/api/setup/status")
    assert resp.status_code == 200
    data = resp.json()
    # Before any setup, should return working status
    assert "status" in data or "done" in data or "error" in data
