from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ezsk.updater import UpdateChecker, _parse_version, current_version


def test_current_version_returns_string():
    v = current_version()
    assert isinstance(v, str)
    assert len(v) > 0


def test_parse_version_basic():
    assert _parse_version("0.1.0") == (0, 1, 0)
    assert _parse_version("1.2.3") == (1, 2, 3)


def test_parse_version_with_v_prefix():
    assert _parse_version("v0.2.1") == (0, 2, 1)


def test_parse_version_comparison():
    assert _parse_version("0.2.0") > _parse_version("0.1.0")
    assert _parse_version("1.0.0") > _parse_version("0.99.99")
    assert _parse_version("0.1.0") == _parse_version("v0.1.0")


def test_checker_unchecked():
    uc = UpdateChecker()
    d = uc.to_dict()
    assert d == {"checked": False}


def test_checker_detects_update(monkeypatch):
    """Update available when remote version is newer."""
    monkeypatch.setattr("ezsk.updater.current_version", lambda: "0.1.0")

    fake_response = json.dumps({
        "tag_name": "v0.2.0",
        "body": "New features",
        "html_url": "https://github.com/ezsk-dev/ezsk/releases/tag/v0.2.0",
    }).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_response
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    uc = UpdateChecker()
    with patch("ezsk.updater.urlopen", return_value=mock_resp):
        uc._do_check()

    assert uc.update_available is True
    assert uc.latest_version == "0.2.0"

    d = uc.to_dict()
    assert d["checked"] is True
    assert d["update_available"] is True


def test_checker_no_update_when_current(monkeypatch):
    """No update when remote version equals current."""
    monkeypatch.setattr("ezsk.updater.current_version", lambda: "0.1.0")

    fake_response = json.dumps({
        "tag_name": "v0.1.0",
        "body": "",
        "html_url": "",
    }).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_response
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    uc = UpdateChecker()
    with patch("ezsk.updater.urlopen", return_value=mock_resp):
        uc._do_check()

    assert uc.update_available is False
