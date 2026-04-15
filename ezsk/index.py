from __future__ import annotations
import json
import os
import re
from ezsk.normalize import normalize_query

INDEX_VERSION = 1
AUDIO_EXTENSIONS = {".flac", ".mp3", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".wma", ".ape", ".alac"}
MIN_TOKENS = 2  # guard against false positives from single-word queries


def _index_path(output_dir: str) -> str:
    return os.path.join(output_dir, ".ezsk_index.json")


def _normalize_for_index(text: str) -> str:
    """Normalize a string for index key comparison.
    Strips hyphens so 'Artist - Title' and 'artist title' produce the same key."""
    n = normalize_query(text)       # NFKD, lowercase, non-word (except -) → space
    n = n.replace("-", " ")         # also collapse hyphens
    return re.sub(r"\s+", " ", n).strip()


def build_index(output_dir: str) -> dict[str, str]:
    """Full scan of output_dir → {normalized_key: file_path}."""
    index: dict[str, str] = {}
    if not os.path.isdir(output_dir):
        return index
    for fname in os.listdir(output_dir):
        stem, ext = os.path.splitext(fname)
        if ext.lower() not in AUDIO_EXTENSIONS:
            continue
        index[_normalize_for_index(stem)] = os.path.join(output_dir, fname)
    return index


def load_index(output_dir: str) -> dict[str, str]:
    """Load persisted index; fall back to full scan if missing or version mismatch."""
    path = _index_path(output_dir)
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            if data.get("version") == INDEX_VERSION:
                return data["entries"]
        except Exception:
            pass
    return build_index(output_dir)


def save_index(output_dir: str, index: dict[str, str]) -> None:
    os.makedirs(output_dir, exist_ok=True)
    path = _index_path(output_dir)
    with open(path, "w") as f:
        json.dump({"version": INDEX_VERSION, "entries": index}, f, indent=2)


def index_add(index: dict[str, str], output_dir: str, query: str, file_path: str) -> None:
    """Add a newly downloaded file to the in-memory index and persist."""
    index[_normalize_for_index(query)] = file_path
    save_index(output_dir, index)


def _token_containment(s1: str, s2: str) -> float:
    """Fraction of the shorter string's tokens that appear in the longer string.
    Handles suffix variants (Remaster, Deluxe, etc.) naturally — extra tokens
    in the longer string don't penalize the score."""
    t1, t2 = set(s1.split()), set(s2.split())
    shorter, longer = (t1, t2) if len(t1) <= len(t2) else (t2, t1)
    if not shorter:
        return 0.0
    return len(shorter & longer) / len(shorter)


def find_in_index(index: dict[str, str], query: str, threshold: float = 0.85) -> str | None:
    """Return path of first index entry fuzzy-matching query above threshold, or None."""
    key = _normalize_for_index(query)
    tokens = key.split()
    if len(tokens) < MIN_TOKENS:
        return None  # too short to match reliably
    for indexed_key, path in index.items():
        if _token_containment(key, indexed_key) >= threshold:
            return path
    return None
