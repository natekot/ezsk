"""Auto-update checker for ezsk.

Checks GitHub releases on startup and exposes the result via a simple API
so the web UI can show an update banner.
"""
from __future__ import annotations

import json
import threading
import time
from importlib.metadata import version as pkg_version
from urllib.request import urlopen

from ezsk.display import print_verbose

GITHUB_REPO = "natekot/ezsk"
CHECK_INTERVAL = 86400  # 24 hours


def current_version() -> str:
    try:
        return pkg_version("ezsk")
    except Exception:
        from ezsk import __version__
        return __version__


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse version string like '0.2.1' into a comparable tuple."""
    v = v.lstrip("v")
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts) or (0,)


class UpdateChecker:
    """Background thread that checks for new releases."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.latest_version: str | None = None
        self.download_url: str | None = None
        self.release_notes: str | None = None
        self.update_available: bool = False
        self._checked = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._check_loop, daemon=True)
        self._thread.start()

    def _check_loop(self) -> None:
        # Initial check after a short delay (don't slow startup)
        time.sleep(5)
        self._do_check()
        # Then check periodically
        while True:
            time.sleep(CHECK_INTERVAL)
            self._do_check()

    def _do_check(self) -> None:
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            with urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())

            tag = data.get("tag_name", "")
            current = current_version()

            self.latest_version = tag.lstrip("v")
            self.release_notes = data.get("body", "")[:500]
            self.download_url = data.get("html_url", "")

            if _parse_version(self.latest_version) > _parse_version(current):
                self.update_available = True
                print_verbose(
                    f"Update available: {current} → {self.latest_version}",
                    self.verbose,
                )
            else:
                self.update_available = False

            self._checked = True
        except Exception as e:
            print_verbose(f"Update check failed: {e}", self.verbose)

    def to_dict(self) -> dict:
        if not self._checked:
            return {"checked": False}
        return {
            "checked": True,
            "update_available": self.update_available,
            "current_version": current_version(),
            "latest_version": self.latest_version,
            "download_url": self.download_url,
            "release_notes": self.release_notes,
        }
