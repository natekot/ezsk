from __future__ import annotations

import math
import os

from ezsk.matching import (
    is_various_artists,
    significant_words,
    word_matches,
)
from ezsk.normalize import normalize_query

AUDIO_EXTENSIONS = {"flac", "mp3", "ogg", "opus", "m4a", "aac", "wav", "wma", "ape", "alac"}
LOSSLESS_EXTENSIONS = {"flac", "wav", "ape", "alac"}

# Tiered artist-word coverage required for a candidate to pass the artist gate.
_ARTIST_STRICT_MAX_WORDS = 2  # ≤ this many significant artist words: require all
_ARTIST_LOOSE_RATIO = 0.75    # > strict_max: require this fraction

# Significant title-word coverage required in filename when artist_filter is set.
_TITLE_MIN_RATIO = 0.60


def _posix_basename(path: str) -> str:
    return path.replace("\\", "/").split("/")[-1]


def _ext(filename: str) -> str:
    return _posix_basename(filename).rsplit(".", 1)[-1].lower() if "." in _posix_basename(filename) else ""


def _filename_only(path: str) -> str:
    return _posix_basename(path).rsplit(".", 1)[0].lower()


def _relevance_score(filename: str, query: str) -> float:
    normalized_name = normalize_query(_filename_only(filename))
    normalized_query = normalize_query(query)
    query_words = normalized_query.split()
    if not query_words:
        return 0
    matches = sum(1 for w in query_words if word_matches(w, normalized_name))
    return (matches / len(query_words)) * 50


def _artist_match(filepath: str, artist: str) -> float:
    """Score artist presence in the full file path, with a hard-reject gate.

    Returns +40 for a full match, proportional for partial passes, or -1 to
    hard-reject when coverage is below the tiered threshold. Uses
    word-boundary matching so "little" does not match "littler" or
    "Little Girl" adjacent to other words.
    """
    if is_various_artists(artist):
        return 0  # compilations: artist tag is untrustworthy; skip gate

    normalized_path = normalize_query(filepath)
    significant = significant_words(artist)

    if not significant:
        # Artist was entirely stopwords (e.g., "The"). Fall back to the old
        # permissive behavior: any original artist word must appear.
        raw_words = normalize_query(artist).split()
        if not raw_words:
            return 0
        matches = sum(1 for w in raw_words if word_matches(w, normalized_path))
        return -1 if matches == 0 else (matches / len(raw_words)) * 40

    matches = sum(1 for w in significant if word_matches(w, normalized_path))
    total = len(significant)
    if total <= _ARTIST_STRICT_MAX_WORDS:
        required = total
    else:
        required = math.ceil(total * _ARTIST_LOOSE_RATIO)
    if matches < required:
        return -1
    return (matches / total) * 40


def _title_coverage_ok(filename: str, query: str) -> bool:
    """Title-only gate: require most significant title words in the filename."""
    title = query.split(" - ", 1)[1] if " - " in query else query
    significant = significant_words(title, drop_fillers=True)
    if not significant:
        return True  # nothing substantive to gate on

    normalized_name = normalize_query(_filename_only(filename))
    matches = sum(1 for w in significant if word_matches(w, normalized_name))
    return (matches / len(significant)) >= _TITLE_MIN_RATIO


def score_file(
    file: dict, user: dict, query: str, preferred_format: str = "flac",
    min_bitrate: int = 320, min_bit_depth: int = 16,
    artist_filter: str = "",
) -> float:
    filename = file.get("filename", "")
    ext = _ext(filename)
    if ext not in AUDIO_EXTENSIONS:
        return -1

    bitrate = file.get("bitRate", 0)
    bit_depth = file.get("bitDepth", 0)

    if ext in LOSSLESS_EXTENSIONS:
        if bit_depth and bit_depth < min_bit_depth:
            return -1
    else:
        if bitrate and bitrate < min_bitrate:
            return -1

    # Artist filter: word-bounded, tiered-coverage gate
    if artist_filter:
        artist_score = _artist_match(filename, artist_filter)
        if artist_score == -1:
            return -1
        # Title-only search: also gate on title-word coverage in the filename
        if not _title_coverage_ok(filename, query):
            return -1
    else:
        artist_score = 0

    score = 0.0

    if ext == preferred_format.lower():
        score += 100
    if ext in LOSSLESS_EXTENSIONS:
        score += 50

    if bitrate:
        score += min(bitrate / 1100, 1.0) * 30

    score += _relevance_score(filename, query)
    score += artist_score

    if user.get("hasFreeUploadSlot", False):
        score += 20

    upload_speed = user.get("uploadSpeed", 0)
    if upload_speed > 0:
        score += min(upload_speed / 5_000_000, 1.0) * 20

    queue_length = user.get("queueLength", 0)
    if queue_length > 50:
        score -= min(queue_length / 10, 30)

    if file.get("isLocked", False):
        score -= 50

    return score


def rank_results(
    responses: list[dict], query: str, preferred_format: str = "flac",
    min_bitrate: int = 320, min_bit_depth: int = 16,
    artist_filter: str = "",
) -> list[dict]:
    candidates = []
    for user_response in responses:
        username = user_response.get("username", "")
        for f in user_response.get("files", []):
            s = score_file(f, user_response, query, preferred_format, min_bitrate, min_bit_depth, artist_filter=artist_filter)
            if s < 0:
                continue
            candidates.append({
                "username": username,
                "filename": f["filename"],
                "size": f.get("size", 0),
                "extension": _ext(f["filename"]),
                "bitrate": f.get("bitRate", 0),
                "bit_depth": f.get("bitDepth", 0),
                "sample_rate": f.get("sampleRate", 0),
                "length": f.get("length", 0),
                "free_slot": user_response.get("hasFreeUploadSlot", False),
                "upload_speed": user_response.get("uploadSpeed", 0),
                "queue_length": user_response.get("queueLength", 0),
                "score": s,
            })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates
