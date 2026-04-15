from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Redirect platformdirs config to a temp directory."""
    d = tmp_path / "config"
    d.mkdir()
    monkeypatch.setattr("ezsk.paths.user_config_dir", lambda *a, **kw: str(d))
    return d


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Redirect platformdirs data to a temp directory."""
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr("ezsk.paths.user_data_dir", lambda *a, **kw: str(d))
    return d


@pytest.fixture
def mock_config(tmp_path):
    """Create a minimal Config pointing at temp dirs."""
    from ezsk.config import Config
    return Config(
        url="http://localhost:5030",
        api_key="test-api-key-that-is-long-enough",
        downloads_dir=str(tmp_path / "downloads"),
        output_dir=str(tmp_path / "output"),
        state_file=str(tmp_path / "state.json"),
        log_file=str(tmp_path / "ezsk.log"),
    )
