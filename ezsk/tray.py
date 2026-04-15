"""System tray icon for ezsk desktop mode.

macOS: uses rumps (native NSStatusBar)
Windows: uses pystray (win32)
Fallback: no tray icon if neither is available
"""
from __future__ import annotations

import platform
import threading
import webbrowser


def _open_ui(port: int = 8642) -> None:
    webbrowser.open(f"http://127.0.0.1:{port}")


def run_tray(port: int = 8642, on_quit=None) -> threading.Thread | None:
    """Start the tray icon in a background thread.

    Returns the thread, or None if tray is unavailable.
    `on_quit` is called when the user selects Quit from the tray menu.
    """
    system = platform.system()

    if system == "Darwin":
        return _run_macos_tray(port, on_quit)
    elif system == "Windows":
        return _run_windows_tray(port, on_quit)
    return None


def _run_macos_tray(port: int, on_quit) -> threading.Thread | None:
    try:
        import rumps
    except ImportError:
        return None

    class EzskApp(rumps.App):
        def __init__(self):
            super().__init__("ezsk", title="♫")

        @rumps.clicked("Open ezsk")
        def open_ui(self, _):
            _open_ui(port)

        @rumps.clicked("Quit")
        def quit_app(self, _):
            if on_quit:
                on_quit()
            rumps.quit_application()

    def _run():
        EzskApp().run()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _run_windows_tray(port: int, on_quit) -> threading.Thread | None:
    try:
        import pystray
        from PIL import Image
    except ImportError:
        return None

    # Create a simple icon (blue circle)
    img = Image.new("RGB", (64, 64), color=(92, 124, 250))

    def _on_open(icon, item):
        _open_ui(port)

    def _on_quit(icon, item):
        icon.stop()
        if on_quit:
            on_quit()

    menu = pystray.Menu(
        pystray.MenuItem("Open ezsk", _on_open, default=True),
        pystray.MenuItem("Quit", _on_quit),
    )
    icon = pystray.Icon("ezsk", img, "ezsk", menu)

    t = threading.Thread(target=icon.run, daemon=True)
    t.start()
    return t
