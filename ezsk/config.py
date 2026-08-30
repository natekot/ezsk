from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ezsk.paths import (
    default_config_path,
    default_dashboard_path,
    default_downloads_dir,
    default_log_path,
    default_state_path,
)

DEFAULT_CONFIG_PATH = default_config_path()

DEFAULT_CONFIG_TEMPLATE = """\
[slskd]
url = "http://localhost:5030"
api_key = ""
downloads_dir = ""
shared_dir = ""
soulseek_username = ""

[output]
dir = "./downloads"
pattern = "{artist} - {title}.{ext}"
format = "flac"
min_bitrate = 320
min_bit_depth = 16
verify = false
min_frequency_cutoff = 19.0
title_only_search = true
strict_metadata_check = true

[anthropic]
api_key = ""
watch_dir = ""
"""


@dataclass
class Config:
    url: str = "http://localhost:5030"
    api_key: str = ""
    downloads_dir: str = ""
    shared_dir: str = ""
    output_dir: str = "./downloads"
    output_pattern: str = "{artist} - {title}.{ext}"
    preferred_format: str = "flac"
    min_bitrate: int = 320
    min_bit_depth: int = 16
    verify: bool = False
    min_frequency_cutoff: float = 19.0
    title_only_search: bool = True
    strict_metadata_check: bool = True
    interactive: bool = False
    verbose: bool = False
    timeout: int = 300
    state_file: str = ""
    log_file: str = ""
    soulseek_username: str = ""
    anthropic_api_key: str = ""
    watch_dir: str = ""
    index: dict[str, str] | None = None  # populated at runtime by --index flag

    def __post_init__(self) -> None:
        if not self.state_file:
            self.state_file = str(default_state_path())
        if not self.log_file:
            self.log_file = str(default_log_path())

    @classmethod
    def from_file(cls, path: Path | None = None) -> Config:
        path = path or DEFAULT_CONFIG_PATH
        cfg = cls()
        if not path.exists():
            return cfg
        with open(path, "rb") as f:
            data = tomllib.load(f)
        slskd = data.get("slskd", {})
        output = data.get("output", {})
        docker = data.get("docker", {})
        cfg.url = slskd.get("url", cfg.url)
        cfg.api_key = slskd.get("api_key", cfg.api_key)
        cfg.downloads_dir = slskd.get("downloads_dir", cfg.downloads_dir)
        cfg.shared_dir = slskd.get("shared_dir", cfg.shared_dir)
        cfg.output_dir = output.get("dir", cfg.output_dir)
        cfg.output_pattern = output.get("pattern", cfg.output_pattern)
        cfg.preferred_format = output.get("format", cfg.preferred_format)
        cfg.min_bitrate = output.get("min_bitrate", cfg.min_bitrate)
        cfg.min_bit_depth = output.get("min_bit_depth", cfg.min_bit_depth)
        cfg.verify = output.get("verify", cfg.verify)
        cfg.min_frequency_cutoff = output.get("min_frequency_cutoff", cfg.min_frequency_cutoff)
        cfg.title_only_search = output.get("title_only_search", cfg.title_only_search)
        cfg.strict_metadata_check = output.get("strict_metadata_check", cfg.strict_metadata_check)
        # Falls back to the pre-0.2 [docker] section for existing configs.
        cfg.soulseek_username = slskd.get(
            "soulseek_username",
            docker.get("soulseek_username", cfg.soulseek_username),
        )
        anthropic = data.get("anthropic", {})
        cfg.anthropic_api_key = anthropic.get("api_key", cfg.anthropic_api_key)
        cfg.watch_dir = anthropic.get("watch_dir", cfg.watch_dir)
        return cfg

    def apply_env(self) -> None:
        self.url = os.environ.get("EZSK_SLSKD_URL", self.url)
        self.api_key = os.environ.get("EZSK_SLSKD_API_KEY", self.api_key)
        self.downloads_dir = os.environ.get("EZSK_SLSKD_DOWNLOADS_DIR", self.downloads_dir)
        self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", self.anthropic_api_key)
        self.watch_dir = os.environ.get("EZSK_WATCH_DIR", self.watch_dir)

    def apply_args(self, args) -> None:
        if args.url:
            self.url = args.url
        if args.api_key:
            self.api_key = args.api_key
        if args.output_dir:
            self.output_dir = args.output_dir
        if args.output_pattern:
            self.output_pattern = args.output_pattern
        if args.format:
            self.preferred_format = args.format
        if args.min_bitrate is not None:
            self.min_bitrate = args.min_bitrate
        if args.min_bit_depth is not None:
            self.min_bit_depth = args.min_bit_depth
        if args.slskd_downloads_dir:
            self.downloads_dir = args.slskd_downloads_dir
        if args.shared_dir:
            self.shared_dir = args.shared_dir
        if args.timeout:
            self.timeout = args.timeout
        if args.state_file:
            self.state_file = args.state_file
        if args.log_file:
            self.log_file = args.log_file
        if args.anthropic_api_key:
            self.anthropic_api_key = args.anthropic_api_key
        if args.watch_dir:
            self.watch_dir = args.watch_dir
        if getattr(args, "verify", False):
            self.verify = True
        if getattr(args, "min_frequency_cutoff", None) is not None:
            self.min_frequency_cutoff = args.min_frequency_cutoff
        if getattr(args, "no_strict_metadata_check", False):
            self.strict_metadata_check = False
        self.interactive = args.interactive
        self.verbose = args.verbose

    SENSITIVE_FIELDS = {"api_key", "anthropic_api_key"}

    def to_dict(self) -> dict:
        return {
            "slskd": {
                "url": self.url,
                "api_key": self.api_key,
                "downloads_dir": self.downloads_dir,
                "shared_dir": self.shared_dir,
                "soulseek_username": self.soulseek_username,
            },
            "output": {
                "dir": self.output_dir,
                "pattern": self.output_pattern,
                "format": self.preferred_format,
                "min_bitrate": self.min_bitrate,
                "min_bit_depth": self.min_bit_depth,
                "verify": self.verify,
                "min_frequency_cutoff": self.min_frequency_cutoff,
                "title_only_search": self.title_only_search,
                "strict_metadata_check": self.strict_metadata_check,
            },
            "anthropic": {
                "api_key": self.anthropic_api_key,
                "watch_dir": self.watch_dir,
            },
        }

    def update_from_dict(self, data: dict) -> None:
        slskd = data.get("slskd", {})
        output = data.get("output", {})
        docker = data.get("docker", {})
        anthropic = data.get("anthropic", {})
        if "url" in slskd:
            self.url = slskd["url"]
        if "api_key" in slskd:
            self.api_key = slskd["api_key"]
        if "downloads_dir" in slskd:
            self.downloads_dir = slskd["downloads_dir"]
        if "shared_dir" in slskd:
            self.shared_dir = slskd["shared_dir"]
        if "dir" in output:
            self.output_dir = output["dir"]
        if "pattern" in output:
            self.output_pattern = output["pattern"]
        if "format" in output:
            self.preferred_format = output["format"]
        if "min_bitrate" in output:
            self.min_bitrate = output["min_bitrate"]
        if "min_bit_depth" in output:
            self.min_bit_depth = output["min_bit_depth"]
        if "verify" in output:
            self.verify = output["verify"]
        if "min_frequency_cutoff" in output:
            self.min_frequency_cutoff = output["min_frequency_cutoff"]
        if "title_only_search" in output:
            self.title_only_search = output["title_only_search"]
        if "strict_metadata_check" in output:
            self.strict_metadata_check = output["strict_metadata_check"]
        if "soulseek_username" in slskd:
            self.soulseek_username = slskd["soulseek_username"]
        elif "soulseek_username" in docker:
            self.soulseek_username = docker["soulseek_username"]
        if "api_key" in anthropic:
            self.anthropic_api_key = anthropic["api_key"]
        if "watch_dir" in anthropic:
            self.watch_dir = anthropic["watch_dir"]

    def save(self, path: Path | None = None) -> None:
        path = path or DEFAULT_CONFIG_PATH
        d = self.to_dict()
        lines = []
        for section, values in d.items():
            lines.append(f"[{section}]")
            for key, val in values.items():
                if isinstance(val, bool):
                    lines.append(f"{key} = {'true' if val else 'false'}")
                elif isinstance(val, int):
                    lines.append(f"{key} = {val}")
                elif isinstance(val, float):
                    lines.append(f"{key} = {val}")
                else:
                    lines.append(f'{key} = "{val}"')
            lines.append("")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines))

    def validate(self) -> list[str]:
        errors = []
        if not self.api_key:
            errors.append(
                "slskd API key is required. "
                "Run 'ezsk --setup' or set via --api-key / EZSK_SLSKD_API_KEY"
            )
        elif not (16 <= len(self.api_key) <= 255):
            errors.append("slskd API key must be between 16 and 255 characters")
        if not self.downloads_dir:
            errors.append(
                "slskd downloads directory is required — this is the folder where slskd "
                "saves files (not your output folder). "
                "Run 'ezsk --setup' or set via --slskd-downloads-dir"
            )
        return errors


def init_config(path: Path | None = None) -> None:
    path = path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        print(f"Config already exists: {path}")
        return
    path.write_text(DEFAULT_CONFIG_TEMPLATE)
    print(f"Created config: {path}")
    print("Edit it to add your slskd API key and downloads directory.")
