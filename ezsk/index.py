from __future__ import annotations
import json
import os
import re
from ezsk.normalize import normalize_query

INDEX_VERSION = 2
AUDIO_EXTENSIONS = {".flac", ".mp3", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".wma", ".ape", ".alac"}
MIN_TOKENS = 2  # guard against false positives from single-word queries

# Files we never want to treat as audio even if scanned by accident
_SKIP_FILES = {".ezsk_index.json", "queued.txt", "dead.txt"}


def _index_path(output_dir: str) -> str:
    return os.path.join(output_dir, ".ezsk_index.json")


def _normalize_for_index(text: str) -> str:
    """Normalize a string for index key comparison.
    Strips hyphens so 'Artist - Title' and 'artist title' produce the same key."""
    n = normalize_query(text)       # NFKD, lowercase, non-word (except -) → space
    n = n.replace("-", " ")         # also collapse hyphens
    return re.sub(r"\s+", " ", n).strip()


def _tag_key_for(path: str) -> str | None:
    """Read embedded artist/title tags and produce an index key.

    Used as a fallback when the filename stem doesn't carry artist+title.
    Returns None if mutagen is unavailable, the file can't be read, or tags
    are missing.
    """
    try:
        import mutagen
    except ImportError:
        return None
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        return None
    if f is None:
        return None
    artist = (f.get("artist", [""])[0] or "").strip()
    title = (f.get("title", [""])[0] or "").strip()
    if not artist or not title:
        return None
    return _normalize_for_index(f"{artist} {title}")


def build_index(output_dir: str) -> dict[str, str]:
    """Recursive scan of output_dir → {normalized_key: file_path}.

    Each audio file contributes its filename-derived key. When the filename
    stem doesn't already contain ' - ' (i.e., it doesn't follow the
    `{artist} - {title}` pattern ezsk produces), we additionally read the
    embedded tags and add a tag-derived key pointing to the same path.
    """
    index: dict[str, str] = {}
    if not os.path.isdir(output_dir):
        return index
    for root, _dirs, files in os.walk(output_dir):
        for fname in files:
            if fname in _SKIP_FILES:
                continue
            stem, ext = os.path.splitext(fname)
            if ext.lower() not in AUDIO_EXTENSIONS:
                continue
            path = os.path.join(root, fname)
            index[_normalize_for_index(stem)] = path
            if " - " not in stem:
                tag_key = _tag_key_for(path)
                if tag_key:
                    index.setdefault(tag_key, path)
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


def index_remove(index: dict[str, str], output_dir: str, query: str) -> None:
    """Drop a query's entry from the in-memory index and persist if changed."""
    if index.pop(_normalize_for_index(query), None) is not None:
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
    """Return path of the first index entry that fuzzy-matches query above
    threshold *and still exists on disk*, or None.

    Files leave the library after they were indexed -- moved into a music
    collection, renamed, deleted -- and the persisted index keeps pointing at
    them. Callers treat a hit as "already downloaded", so an unchecked path
    marks a track complete that was never fetched. Keep scanning past a stale
    hit rather than giving up: the same track is often present under a
    different name. Purely a read -- pruning here would wipe the index whenever
    the library sits on an unmounted volume; `--reindex` and
    POST /api/library/rescan rebuild it deliberately.
    """
    key = _normalize_for_index(query)
    tokens = key.split()
    if len(tokens) < MIN_TOKENS:
        return None  # too short to match reliably
    for indexed_key, path in index.items():
        if _token_containment(key, indexed_key) >= threshold and os.path.exists(path):
            return path
    return None


def check_queries(
    index: dict[str, str], queries: list[str], threshold: float = 0.85,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Partition queries into (have, missing).

    `have` is a list of (query, file_path) tuples for queries already in the
    library. `missing` is the list of queries that aren't.
    """
    have: list[tuple[str, str]] = []
    missing: list[str] = []
    for q in queries:
        path = find_in_index(index, q, threshold=threshold)
        if path:
            have.append((q, path))
        else:
            missing.append(q)
    return have, missing
