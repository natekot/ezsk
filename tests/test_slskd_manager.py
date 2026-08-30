from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import ezsk.slskd_manager as mgr


def test_platform_suffix():
    """_platform_suffix returns a valid platform string."""
    suffix = mgr._platform_suffix()
    parts = suffix.split("-")
    assert len(parts) == 2
    assert parts[0] in ("osx", "win", "linux")
    assert parts[1] in ("x64", "arm64", "arm")


def test_bundled_binary_none_in_normal_python():
    """No bundled binary in normal (non-PyInstaller) Python."""
    assert mgr._bundled_binary_path() is None


def test_bundled_binary_found_with_meipass(tmp_path, monkeypatch):
    """Bundled binary is found when sys._MEIPASS is set."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    name = "slskd.exe" if platform.system() == "Windows" else "slskd"
    (bin_dir / name).write_text("fake binary")

    # _MEIPASS doesn't exist normally — use setattr with create=True via direct assignment
    sys._MEIPASS = str(tmp_path)
    try:
        result = mgr._bundled_binary_path()
        assert result is not None
        assert result.name == name
    finally:
        del sys._MEIPASS


def test_slskd_binary_path_prefers_bundled(tmp_path, monkeypatch):
    """_slskd_binary_path returns bundled path when available."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    name = "slskd.exe" if platform.system() == "Windows" else "slskd"
    (bin_dir / name).write_text("fake")
    sys._MEIPASS = str(tmp_path)
    try:
        result = mgr._slskd_binary_path()
        assert str(tmp_path) in str(result)
    finally:
        del sys._MEIPASS


def test_slskd_binary_path_falls_back_to_data_dir(tmp_path, monkeypatch):
    """Without bundled binary, path points to data dir."""
    monkeypatch.setattr(mgr, "data_dir", lambda: tmp_path)
    # Ensure no _MEIPASS
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    result = mgr._slskd_binary_path()
    assert str(tmp_path / "bin") in str(result)


def test_is_installed_false_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "data_dir", lambda: tmp_path)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert mgr.is_installed() is False


def test_generate_config_writes_yaml(tmp_path, monkeypatch, mock_config):
    """generate_config produces a slskd.yml with expected content."""
    monkeypatch.setattr(mgr, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(mgr, "default_downloads_dir", lambda: tmp_path / "dl")

    path = mgr.generate_config(mock_config, "testuser", "testpass")
    assert path.exists()

    content = path.read_text()
    assert "testuser" in content
    assert "testpass" in content
    assert "api_keys" in content
    assert mock_config.api_key in content


def test_download_skips_when_bundled(tmp_path):
    """download_slskd returns immediately when bundled binary exists."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    name = "slskd.exe" if platform.system() == "Windows" else "slskd"
    fake_binary = bin_dir / name
    fake_binary.write_text("fake")

    sys._MEIPASS = str(tmp_path)
    try:
        result = mgr.download_slskd(verbose=False)
        assert result == fake_binary
    finally:
        del sys._MEIPASS


def _fake_release_zip(tmp_path: Path, binary_name: str) -> bytes:
    """A zip shaped like a real slskd release: binary plus sibling assets."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(binary_name, "fake binary")
        zf.writestr("wwwroot/index.html", "<html></html>")
        zf.writestr("config/slskd.example.yml", "example: true")
        zf.writestr("etc/nothing", "")
        zf.writestr("LICENSE", "license text")
    return buf.getvalue()


def _patch_download(monkeypatch, tmp_path, binary_name):
    """Point slskd_manager at tmp_path and stub out the network."""
    monkeypatch.setattr(mgr, "data_dir", lambda: tmp_path)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(
        mgr, "_latest_release", lambda: ("0.99.0", "https://example.com/slskd.zip")
    )

    payload = _fake_release_zip(tmp_path, binary_name)
    mock_resp = MagicMock()
    mock_resp.headers = {"Content-Length": str(len(payload))}
    mock_resp.read.side_effect = [payload, b""]
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return patch.object(mgr, "urlopen", return_value=mock_resp)


def test_download_extracts_full_release(tmp_path, monkeypatch):
    """The whole release lands next to the binary.

    slskd resolves wwwroot/config/etc relative to its own directory and exits
    with 'ContentPath specifies a non-existent directory' without them.
    """
    name = "slskd.exe" if platform.system() == "Windows" else "slskd"

    with _patch_download(monkeypatch, tmp_path, name):
        result = mgr.download_slskd(verbose=False)

    bin_dir = tmp_path / "bin"
    assert result == bin_dir / name
    assert result.exists()
    assert (bin_dir / "wwwroot" / "index.html").exists()
    assert (bin_dir / "config" / "slskd.example.yml").exists()
    assert (bin_dir / "LICENSE").exists()
    # Scratch space is cleaned up
    assert not (bin_dir / "extract").exists()
    assert not (bin_dir / "slskd-download.zip").exists()
    assert mgr.installed_version() == "0.99.0"


def test_download_reinstalls_when_wwwroot_missing(tmp_path, monkeypatch):
    """A binary-only install (from the old extraction) is repaired, not reused."""
    name = "slskd.exe" if platform.system() == "Windows" else "slskd"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / name).write_text("stale binary")
    (bin_dir / "version.txt").write_text("0.99.0")

    with _patch_download(monkeypatch, tmp_path, name):
        mgr.download_slskd(verbose=False)

    assert (bin_dir / "wwwroot" / "index.html").exists()
    assert (bin_dir / name).read_text() == "fake binary"


def test_latest_release_parses_response(monkeypatch):
    """_latest_release correctly parses the GitHub API response."""
    suffix = mgr._platform_suffix()
    fake_response = json.dumps({
        "tag_name": "0.99.0",
        "assets": [
            {
                "name": f"slskd-0.99.0-{suffix}.zip",
                "browser_download_url": f"https://example.com/slskd-0.99.0-{suffix}.zip",
            }
        ],
    }).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_response
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch.object(mgr, "urlopen", return_value=mock_resp):
        tag, url = mgr._latest_release()

    assert tag == "0.99.0"
    assert suffix in url
