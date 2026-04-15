"""Desktop entry point for ezsk.

Starts the managed slskd process (if configured), launches the web UI,
and opens the default browser. This is the entry point for the bundled
desktop application (.app / .exe).
"""
from __future__ import annotations

import signal
import sys
import webbrowser


def main() -> None:
    from ezsk.config import Config, DEFAULT_CONFIG_PATH
    from ezsk.display import print_status, set_log_file

    config = Config.from_file()
    config.apply_env()

    if config.log_file:
        set_log_file(config.log_file)

    # Load index
    from ezsk.index import load_index
    config.index = load_index(config.output_dir)

    port = 8642

    # Start managed slskd if config exists and has credentials
    if DEFAULT_CONFIG_PATH.exists() and config.api_key:
        from ezsk.paths import data_dir
        slskd_config = data_dir() / "slskd" / "slskd.yml"
        if slskd_config.exists():
            print_status("Starting slskd...")
            try:
                from ezsk.slskd_manager import ensure_running
                ensure_running(config)
            except Exception as e:
                print_status(f"slskd startup failed: {e} (continuing without managed slskd)")

    # Graceful shutdown
    def _shutdown(sig, frame):
        from ezsk.slskd_manager import stop
        stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # System tray icon
    try:
        from ezsk.tray import run_tray
        run_tray(port=port, on_quit=lambda: _shutdown(None, None))
    except Exception:
        pass  # Tray is optional — continue without it

    # Open browser after a short delay (server needs to start first)
    import threading
    def _open_browser():
        import time
        time.sleep(1.5)
        webbrowser.open(f"http://127.0.0.1:{port}")

    threading.Thread(target=_open_browser, daemon=True).start()

    # Start web server (blocks)
    from ezsk.web.app import run_server
    print_status(f"ezsk desktop — http://127.0.0.1:{port}")
    run_server(config, port=port)


if __name__ == "__main__":
    main()
