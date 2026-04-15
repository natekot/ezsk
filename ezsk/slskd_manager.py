"""Manage a local slskd process — download, configure, start, stop.

Replaces the Docker-based approach with direct binary management.
Downloads the platform-appropriate slskd release from GitHub on first
run and caches it in the ezsk data directory.
"""
from __future__ import annotations

import atexit
import os
import platform
import secrets
import signal
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.request import urlopen

from ezsk.config import Config
from ezsk.display import print_error, print_status, print_verbose
from ezsk.paths import data_dir, default_downloads_dir

SLSKD_PORT = 5030
SOULSEEK_LISTEN_PORT = 50300
HEALTH_TIMEOUT = 60


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def _platform_suffix() -> str:
    """Return the slskd release suffix for this OS/arch."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        os_part = "osx"
    elif system == "windows":
        os_part = "win"
    elif system == "linux":
        os_part = "linux"
    else:
        raise RuntimeError(f"Unsupported OS: {system}")

    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine.startswith("arm"):
        arch = "arm"
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}")

    return f"{os_part}-{arch}"


# ---------------------------------------------------------------------------
# Binary management
# ---------------------------------------------------------------------------

def _bundled_binary_path() -> Path | None:
    """Check if slskd is bundled alongside the ezsk executable (PyInstaller)."""
    name = "slskd.exe" if platform.system() == "Windows" else "slskd"

    # PyInstaller sets sys._MEIPASS to the temp extraction dir
    if hasattr(sys, "_MEIPASS"):
        candidate = Path(sys._MEIPASS) / "bin" / name
        if candidate.exists():
            return candidate

    # Also check next to the running executable (onedir mode)
    exe_dir = Path(sys.executable).parent
    candidate = exe_dir / "bin" / name
    if candidate.exists():
        return candidate

    # macOS .app: check Resources/bin/
    if platform.system() == "Darwin":
        resources = exe_dir.parent / "Resources" / "bin" / name
        if resources.exists():
            return resources

    return None


def _bin_dir() -> Path:
    return data_dir() / "bin"


def _slskd_binary_path() -> Path:
    """Return the path to the slskd binary — bundled or downloaded."""
    bundled = _bundled_binary_path()
    if bundled is not None:
        return bundled
    name = "slskd.exe" if platform.system() == "Windows" else "slskd"
    return _bin_dir() / name


def _installed_version_path() -> Path:
    return _bin_dir() / "version.txt"


def is_installed() -> bool:
    return _slskd_binary_path().exists()


def installed_version() -> str | None:
    vp = _installed_version_path()
    if vp.exists():
        return vp.read_text().strip()
    return None


def _latest_release() -> tuple[str, str]:
    """Fetch latest release tag and download URL for this platform."""
    import json

    url = "https://api.github.com/repos/slskd/slskd/releases/latest"
    with urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read())

    tag = data["tag_name"]
    suffix = _platform_suffix()
    target_name = f"slskd-{tag}-{suffix}.zip"

    for asset in data["assets"]:
        if asset["name"] == target_name:
            return tag, asset["browser_download_url"]

    raise RuntimeError(
        f"No slskd release found for {suffix} in version {tag}"
    )


def download_slskd(verbose: bool = False) -> Path:
    """Download and extract slskd binary. Returns path to binary.

    If a bundled binary exists (PyInstaller build), returns it immediately.
    """
    bundled = _bundled_binary_path()
    if bundled is not None:
        print_verbose(f"Using bundled slskd at {bundled}", verbose)
        return bundled

    tag, url = _latest_release()
    bin_path = _bin_dir() / ("slskd.exe" if platform.system() == "Windows" else "slskd")

    if bin_path.exists() and installed_version() == tag:
        print_verbose(f"slskd {tag} already installed", verbose)
        return bin_path

    print_status(f"Downloading slskd {tag}...")
    zip_path = _bin_dir() / "slskd-download.zip"
    _bin_dir().mkdir(parents=True, exist_ok=True)

    # Stream download with progress
    with urlopen(url, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(zip_path, "wb") as f:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    mb = downloaded // (1024 * 1024)
                    total_mb = total // (1024 * 1024)
                    print(f"\r  Downloading: {mb}/{total_mb}MB ({pct}%)", end="", flush=True)
        if total:
            print()  # newline after progress

    # Extract binary
    print_status("Extracting slskd...")
    extract_dir = _bin_dir() / "extract"
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)

    # Find the binary in the extracted files
    binary_name = "slskd.exe" if platform.system() == "Windows" else "slskd"
    extracted_binary = extract_dir / binary_name
    if not extracted_binary.exists():
        raise RuntimeError(f"Binary {binary_name} not found in release zip")

    # Move binary to final location
    if bin_path.exists():
        bin_path.unlink()
    extracted_binary.rename(bin_path)

    # Make executable on Unix
    if platform.system() != "Windows":
        bin_path.chmod(bin_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

    # Save version
    _installed_version_path().write_text(tag)

    # Clean up
    import shutil
    shutil.rmtree(extract_dir, ignore_errors=True)
    zip_path.unlink(missing_ok=True)

    print_status(f"slskd {tag} installed to {bin_path}")
    return bin_path


# ---------------------------------------------------------------------------
# Configuration generation
# ---------------------------------------------------------------------------

def _slskd_app_dir() -> Path:
    """Working directory for the slskd process."""
    d = data_dir() / "slskd"
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate_config(
    config: Config,
    soulseek_username: str,
    soulseek_password: str,
) -> Path:
    """Write slskd.yml and return its path."""
    app_dir = _slskd_app_dir()
    config_path = app_dir / "slskd.yml"

    api_key = config.api_key
    if not api_key:
        api_key = secrets.token_urlsafe(32)
        config.api_key = api_key

    downloads_dir = config.downloads_dir
    if not downloads_dir:
        downloads_dir = str(default_downloads_dir())
        config.downloads_dir = downloads_dir
    Path(downloads_dir).mkdir(parents=True, exist_ok=True)

    web_password = secrets.token_urlsafe(16)

    # Build shares section
    shares_section = ""
    if config.shared_dir and Path(config.shared_dir).is_dir():
        shares_section = f"""
shares:
  directories:
    - {config.shared_dir}
  filters:
    - \\.ini$
    - Thumbs.db$
    - \\.DS_Store$"""
    else:
        shares_section = """
shares:
  filters:
    - \\.ini$
    - Thumbs.db$
    - \\.DS_Store$"""

    content = f"""\
soulseek:
  username: {soulseek_username}
  password: {soulseek_password}
  listen_port: {SOULSEEK_LISTEN_PORT}
directories:
  downloads: {downloads_dir}
{shares_section}
web:
  port: {SLSKD_PORT}
  authentication:
    disabled: false
    username: slskd
    password: {web_password}
    api_keys:
      ezsk:
        key: {api_key}
        role: ReadWrite
"""
    config_path.write_text(content)

    # Save web credentials
    creds_path = app_dir / "web_credentials.txt"
    creds_path.write_text(f"username: slskd\npassword: {web_password}\n")
    if platform.system() != "Windows":
        creds_path.chmod(0o600)

    return config_path


# ---------------------------------------------------------------------------
# Process lifecycle
# ---------------------------------------------------------------------------

_process: subprocess.Popen | None = None


def start(config: Config, verbose: bool = False) -> subprocess.Popen:
    """Start slskd as a subprocess. Downloads binary if needed."""
    global _process

    if _process is not None and _process.poll() is None:
        print_verbose("slskd already running (managed)", verbose)
        return _process

    binary = download_slskd(verbose)
    app_dir = _slskd_app_dir()
    config_path = app_dir / "slskd.yml"

    if not config_path.exists():
        raise RuntimeError(
            "slskd config not found. Run 'ezsk --setup' to configure."
        )

    # slskd expects to be run from its app directory
    env = os.environ.copy()
    env["SLSKD_APP_DIR"] = str(app_dir)

    log_path = app_dir / "slskd.log"

    log_file = open(log_path, "a")
    _process = subprocess.Popen(
        [str(binary), "--app-dir", str(app_dir)],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(app_dir),
        env=env,
    )

    # Register cleanup
    atexit.register(stop)

    print_verbose(f"slskd started (pid {_process.pid})", verbose)
    return _process


def stop() -> None:
    """Gracefully stop the managed slskd process."""
    global _process
    if _process is None:
        return

    if _process.poll() is not None:
        _process = None
        return

    try:
        if platform.system() == "Windows":
            _process.terminate()
        else:
            _process.send_signal(signal.SIGTERM)

        try:
            _process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _process.kill()
            _process.wait(timeout=5)
    except Exception:
        pass
    finally:
        _process = None


def is_running() -> bool:
    return _process is not None and _process.poll() is None


def wait_for_healthy(config: Config, timeout: int = HEALTH_TIMEOUT) -> None:
    """Poll slskd API until it responds, or raise after timeout."""
    from urllib.error import URLError
    from urllib.request import Request

    url = f"{config.url}/api/v0/application"
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            req = Request(url, headers={"X-API-Key": config.api_key})
            with urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    print_status("slskd is ready.")
                    return
        except (URLError, OSError, Exception):
            pass

        # Check if process died
        if _process is not None and _process.poll() is not None:
            rc = _process.returncode
            log_path = _slskd_app_dir() / "slskd.log"
            raise RuntimeError(
                f"slskd exited with code {rc}. Check log: {log_path}"
            )

        time.sleep(2)

    raise RuntimeError(f"slskd did not become healthy within {timeout}s")


def ensure_running(config: Config) -> None:
    """Start slskd if not already running, wait until healthy."""
    start(config, verbose=config.verbose)
    wait_for_healthy(config)
