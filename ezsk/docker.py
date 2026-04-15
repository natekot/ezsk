from __future__ import annotations

import getpass
import os
import secrets
import sys
import time
from pathlib import Path

from ezsk.config import Config
from ezsk.paths import default_downloads_dir, default_slskd_data_dir
from ezsk.display import print_error, print_status, print_verbose

CONTAINER_NAME = "ezsk-slskd"
IMAGE = "slskd/slskd:latest"
SLSKD_PORT = 5030
DATA_DIR_NAME = "slskd-data"


def _get_docker():
    try:
        import docker
    except ImportError:
        print_error("Docker support requires: pip install ezsk[docker]")
        sys.exit(1)

    for base_url in [None, "unix://" + str(Path.home()) + "/.docker/run/docker.sock",
                     "unix:///var/run/docker.sock"]:
        try:
            if base_url is None:
                client = docker.from_env()
            else:
                client = docker.DockerClient(base_url=base_url)
            client.ping()
            return client
        except Exception:
            continue

    print_error("Cannot connect to Docker. Is Docker Desktop running?")
    sys.exit(1)


def _write_slskd_config(
    data_dir: str, api_key: str, username: str, password: str, web_password: str,
) -> str:
    config_path = os.path.join(data_dir, "slskd.yml")
    config_content = f"""\
soulseek:
  username: {username}
  password: {password}
shares:
  filters:
    - \\.ini$
    - Thumbs.db$
    - \\.DS_Store$
web:
  authentication:
    disabled: false
    username: slskd
    password: {web_password}
    api_keys:
      ezsk:
        key: {api_key}
        role: ReadWrite
"""
    os.makedirs(data_dir, exist_ok=True)
    with open(config_path, "w") as f:
        f.write(config_content)
    return config_path


def ensure_slskd_running(config: Config) -> None:
    client = _get_docker()

    try:
        container = client.containers.get(CONTAINER_NAME)
        if container.status == "running":
            print_verbose(f"Container {CONTAINER_NAME} already running", config.verbose)
            _wait_for_healthy(config)
            return
        print_status(f"Starting existing container {CONTAINER_NAME}...")
        container.start()
    except Exception:
        _start_new_container(client, config)

    _wait_for_healthy(config)


def _start_new_container(client, config: Config) -> None:
    username = config.docker_username or os.environ.get("SLSKD_USERNAME", "")
    password = os.environ.get("SLSKD_PASSWORD", "")

    if not username:
        username = input("Soulseek username: ")
    if not password:
        password = getpass.getpass("Soulseek password: ")

    api_key = config.api_key or secrets.token_urlsafe(32)
    if not config.api_key:
        print_status(f"Generated API key: {api_key}")

    data_dir = str(default_slskd_data_dir())
    os.makedirs(data_dir, exist_ok=True)

    downloads_dir = config.downloads_dir
    if not downloads_dir:
        downloads_dir = str(default_downloads_dir())
        config.downloads_dir = downloads_dir
    os.makedirs(downloads_dir, exist_ok=True)

    web_password = secrets.token_urlsafe(16)
    creds_path = os.path.join(data_dir, "web_credentials.txt")
    with open(creds_path, "w") as f:
        f.write(f"username: slskd\npassword: {web_password}\n")
    os.chmod(creds_path, 0o600)
    print_status(f"Web UI credentials — username: slskd, password: {web_password}")
    print_status(f"Credentials saved to {creds_path}")

    _write_slskd_config(data_dir, api_key, username, password, web_password)

    volumes = {
        data_dir: {"bind": "/app", "mode": "rw"},
        downloads_dir: {"bind": "/app/downloads", "mode": "rw"},
    }
    if config.shared_dir and os.path.isdir(config.shared_dir):
        volumes[config.shared_dir] = {"bind": "/app/shared", "mode": "ro"}

    print_status(f"Starting {CONTAINER_NAME}...")
    client.containers.run(
        IMAGE,
        name=CONTAINER_NAME,
        detach=True,
        ports={f"{SLSKD_PORT}/tcp": SLSKD_PORT, "50300/tcp": 50300},
        volumes=volumes,
        restart_policy={"Name": "unless-stopped"},
    )

    if not config.api_key:
        config.api_key = api_key


def _wait_for_healthy(config: Config, timeout: int = 60) -> None:
    import requests

    url = f"{config.url}/api/v0/application"
    headers = {"X-API-Key": config.api_key}
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            r = requests.get(url, headers=headers, timeout=5)
            if r.ok:
                print_status("slskd is ready.")
                return
        except Exception:
            pass
        time.sleep(2)

    print_error("slskd did not become healthy in time")
    sys.exit(1)
