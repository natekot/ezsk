"""Cross-platform path resolution for ezsk.

Uses platformdirs so paths land in the OS-native location:
  macOS:   ~/Library/Application Support/ezsk/
  Windows: %APPDATA%\\ezsk\\
  Linux:   ~/.local/share/ezsk/  (XDG)

On first run, migrates from legacy XDG paths (~/.config/ezsk,
~/.local/share/ezsk) if the new location is empty.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

# Legacy XDG paths (pre-platformdirs)
_LEGACY_CONFIG_DIR = Path.home() / ".config" / "ezsk"
_LEGACY_DATA_DIR = Path.home() / ".local" / "share" / "ezsk"

_migrated = False


def _migrate_if_needed() -> None:
    """Copy files from legacy XDG dirs to platformdirs location (once)."""
    global _migrated
    if _migrated:
        return
    _migrated = True

    new_config = Path(user_config_dir("ezsk"))
    new_data = Path(user_data_dir("ezsk"))

    # Migrate config if legacy exists and new doesn't have a config yet
    legacy_config_file = _LEGACY_CONFIG_DIR / "config.toml"
    new_config_file = new_config / "config.toml"
    if legacy_config_file.exists() and not new_config_file.exists():
        new_config.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_config_file, new_config_file)

    # Migrate state if legacy exists and new doesn't have state yet
    legacy_state = _LEGACY_DATA_DIR / "state.json"
    new_state = new_data / "state.json"
    if legacy_state.exists() and not new_state.exists():
        new_data.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_state, new_state)


def config_dir() -> Path:
    _migrate_if_needed()
    return Path(user_config_dir("ezsk", ensure_exists=True))


def data_dir() -> Path:
    _migrate_if_needed()
    return Path(user_data_dir("ezsk", ensure_exists=True))


def default_config_path() -> Path:
    return config_dir() / "config.toml"


def default_state_path() -> Path:
    return data_dir() / "state.json"


def default_log_path() -> Path:
    return data_dir() / "ezsk.log"


def default_dashboard_path() -> Path:
    return data_dir() / "status.html"


def default_downloads_dir() -> Path:
    return data_dir() / "slskd-downloads"


def default_slskd_data_dir() -> Path:
    return data_dir() / "slskd-data"


def atomic_write_text(path: str | Path, text: str) -> None:
    """Write `text` to `path` so readers never see a half-written file.

    Writes a sibling tempfile and renames it over the target -- os.replace is
    atomic within a filesystem, so a concurrent reader gets either the old file
    or the new one. state.json, the library index, queued.txt/dead.txt, the
    dashboard and config.toml are all rewritten whole from several threads, and
    all of them are read back by the web UI while a batch is running.

    Atomic per write only: it does not make a read-modify-write atomic. Callers
    that need that must hold a lock across both halves (see state.state_txn).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
