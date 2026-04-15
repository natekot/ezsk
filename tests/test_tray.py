from __future__ import annotations


def test_tray_module_imports():
    """Tray module imports without error even without rumps/pystray."""
    from ezsk.tray import run_tray
    assert callable(run_tray)


def test_run_tray_returns_none_without_deps(monkeypatch):
    """run_tray returns None when platform-specific deps are missing."""
    import ezsk.tray as tray_mod

    # Force both platform paths to try importing unavailable modules
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    # rumps is not installed, so _run_macos_tray should return None
    result = tray_mod.run_tray(port=9999)
    assert result is None
