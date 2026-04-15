from __future__ import annotations

import os
import re


def parse_query(query: str) -> dict[str, str]:
    parts = {"artist": "Unknown", "title": query, "ext": ""}
    if " - " in query:
        artist, title = query.split(" - ", 1)
        parts["artist"] = artist.strip()
        parts["title"] = title.strip()
    return parts


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = name.strip(". ")
    return name


def format_output_path(
    output_dir: str, pattern: str, query: str, remote_filename: str
) -> str:
    ext = os.path.splitext(remote_filename)[1].lstrip(".")
    parts = parse_query(query)
    parts["ext"] = ext
    formatted = pattern.format(**parts)
    formatted = sanitize_filename(formatted)
    return os.path.join(output_dir, formatted)
