from __future__ import annotations

import getpass
import os
import secrets
from pathlib import Path

from ezsk.config import DEFAULT_CONFIG_PATH
from ezsk.display import print_error, print_status
from ezsk.paths import default_downloads_dir


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"  {label}{suffix}: ").strip()
    return value or default


def _prompt_yn(label: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    value = input(f"  {label} ({hint}): ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes")


def _prompt_password(label: str) -> str:
    return getpass.getpass(f"  {label}: ")


def run_setup() -> None:
    print()
    print("ezsk setup")
    print("=" * 40)

    if DEFAULT_CONFIG_PATH.exists():
        if not _prompt_yn(f"Config already exists at {DEFAULT_CONFIG_PATH}. Overwrite?"):
            print("  Setup cancelled.")
            return

    # Step 1: slskd mode
    print()
    print("How should ezsk run slskd?")
    print("  1. Automatic (recommended — ezsk downloads and manages slskd)")
    print("  2. Docker (use an existing Docker setup)")
    print("  3. External (I have slskd running already)")
    choice = _prompt("Choice", "1")
    use_managed = choice == "1"
    use_docker = choice == "2"

    url = "http://localhost:5030"
    api_key = ""
    downloads_dir = ""
    shared_dir = ""
    docker_username = ""
    soulseek_username = ""
    soulseek_password = ""

    if use_managed:
        # Managed binary path
        print()
        print("Soulseek account")
        print("  (Create one at https://www.slsknet.org if you don't have one)")
        soulseek_username = _prompt("Soulseek username")
        soulseek_password = _prompt_password("Soulseek password")

        api_key = secrets.token_urlsafe(32)
        downloads_dir = str(default_downloads_dir())
        print()
        print(f"  Downloads dir: {downloads_dir}")
    elif use_docker:
        # Docker path
        print()
        print("Soulseek account")
        print("  (Create one at https://www.slsknet.org if you don't have one)")
        docker_username = _prompt("Soulseek username")
        soulseek_password = _prompt_password("Soulseek password")

        api_key = secrets.token_urlsafe(32)
        downloads_dir = str(default_downloads_dir())
        print()
        print(f"  Generated API key: {api_key}")
        print(f"  Downloads dir: {downloads_dir}")
    else:
        # External slskd path
        print()
        print("slskd connection")
        url = _prompt("slskd URL", "http://localhost:5030")
        print()
        print("  Find your API key in your slskd config (slskd.yml)")
        print("  under: web > authentication > api_keys")
        api_key = _prompt("API key")
        print()
        print("  The slskd downloads directory is where slskd saves files.")
        print("  Check your slskd config or Docker volume mounts.")
        downloads_dir = _prompt("slskd downloads directory")

    # Output directory
    print()
    default_output = str(Path("~/Music/soulseek").expanduser())
    output_dir = _prompt("Output directory (where ezsk saves your music)", default_output)

    # Shared directory
    print()
    if _prompt_yn("Share your output directory with Soulseek users? (improves search results)"):
        shared_dir = output_dir

    # AI features
    anthropic_api_key = ""
    print()
    if _prompt_yn("Enable AI-powered search? (requires Anthropic API key)"):
        anthropic_api_key = _prompt("Anthropic API key")

    # Write config
    config_content = f"""\
[slskd]
url = "{url}"
api_key = "{api_key}"
downloads_dir = "{downloads_dir}"
shared_dir = "{shared_dir}"

[output]
dir = "{output_dir}"
pattern = "{{artist}} - {{title}}.{{ext}}"
format = "flac"
min_bitrate = 320
min_bit_depth = 16
verify = true

[docker]
soulseek_username = "{docker_username}"

[anthropic]
api_key = "{anthropic_api_key}"
"""

    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_CONFIG_PATH.write_text(config_content)
    print()
    print_status(f"Config saved to {DEFAULT_CONFIG_PATH}")

    # Start slskd
    if use_managed:
        print()
        if _prompt_yn("Download and start slskd now?", default=True):
            from ezsk.config import Config
            config = Config.from_file()
            config.apply_env()
            try:
                from ezsk.slskd_manager import download_slskd, generate_config, ensure_running
                download_slskd(verbose=True)
                generate_config(config, soulseek_username, soulseek_password)
                ensure_running(config)
                print_status("slskd is running and connected.")
            except Exception as e:
                print_error(f"Failed to start slskd: {e}")
                print_status("You can start it later with: ezsk --managed 'Artist - Title'")
    elif use_docker:
        print()
        if _prompt_yn("Start slskd Docker container now?", default=True):
            from ezsk.config import Config
            config = Config.from_file()
            config.apply_env()
            os.environ["SLSKD_PASSWORD"] = soulseek_password
            try:
                from ezsk.docker import ensure_slskd_running
                ensure_slskd_running(config)
                print_status("slskd is running and connected.")
            except Exception as e:
                print_error(f"Failed to start slskd: {e}")
                print_status("You can start it later with: ezsk --docker 'Artist - Title'")
    else:
        print()
        print_status("Testing connection...")
        try:
            from ezsk.client import SlskdClient
            from ezsk.config import Config
            config = Config.from_file()
            config.apply_env()
            SlskdClient(config).check_connection()
            print_status("Connected to slskd.")
        except Exception as e:
            print_error(f"Cannot connect to slskd: {e}")
            print_status("Check that slskd is running and the API key is correct.")

    # Next steps
    print()
    flag = " --managed" if use_managed else (" --docker" if use_docker else "")
    print("Next steps:")
    print(f"  ezsk{flag} 'Artist - Title'")
    print(f"  ezsk{flag} -f tracks.txt")
    print(f"  ezsk --status")
    print()
