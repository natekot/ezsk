from __future__ import annotations

import os
import re

from ezsk.normalize import normalize_query

AUDIO_EXTENSIONS = {"flac", "mp3", "ogg", "opus", "m4a", "aac", "wav", "wma", "ape", "alac"}
LOSSLESS_EXTENSIONS = {"flac", "wav", "ape", "alac"}


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
    matches = sum(1 for w in query_words if w in normalized_name)
    return (matches / len(query_words)) * 50


def _artist_match(filepath: str, artist: str) -> float:
    """Check if artist name words appear in the full file path.

    Returns a score: +40 for full match, proportional for partial,
    or -1 sentinel to hard-reject if no artist words match at all.
    """
    normalized_path = normalize_query(filepath)
    artist_words = normalize_query(artist).split()
    if not artist_words:
        return 0
    matches = sum(1 for w in artist_words if w in normalized_path)
    if matches == 0:
        return -1  # sentinel: no artist match at all
    return (matches / len(artist_words)) * 40


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

    # Artist filter: reject files with no artist word matches
    if artist_filter:
        artist_score = _artist_match(filename, artist_filter)
        if artist_score == -1:
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
