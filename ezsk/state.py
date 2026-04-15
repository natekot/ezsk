from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from ezsk.normalize import query_hash
from ezsk.paths import default_state_path

RETRY_CAP = 5
RETRY_COOLDOWN_SECONDS = 86400  # 24 hours

_on_change: list = []


def register_on_change(cb) -> None:
    _on_change.append(cb)


def unregister_on_change(cb) -> None:
    try:
        _on_change.remove(cb)
    except ValueError:
        pass


def _default_state_path() -> str:
    return str(default_state_path())


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        state = json.load(f)
    # Migrate entries whose keys don't match current hash (e.g. after
    # normalize_query changes).  Re-key by query_hash(entry["query"]).
    migrated = {}
    needs_migration = False
    for key, entry in state.items():
        if not isinstance(entry, dict) or "query" not in entry:
            migrated[key] = entry
            continue
        correct_key = query_hash(entry["query"])
        if correct_key != key:
            needs_migration = True
        migrated[correct_key] = entry
    if needs_migration:
        save_state(path, migrated)
    return migrated


def save_state(path: str, state: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=Path(path).parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    for cb in _on_change:
        try:
            cb(state)
        except Exception:
            pass


def is_completed(state: dict, query: str) -> bool:
    key = query_hash(query)
    entry = state.get(key)
    if entry is None or entry.get("status") != "completed":
        return False
    local_path = entry.get("local_path", "")
    if local_path and not os.path.exists(local_path):
        return False
    return True


def update_entry(
    state: dict,
    query: str,
    *,
    status: str,
    username: str = "",
    remote_filename: str = "",
    local_path: str = "",
    error: str = "",
    progress: int = 0,
    total_size: int = 0,
    search_query: str = "",
    transient: bool = False,
) -> dict:
    key = query_hash(query)
    entry = state.get(key, {"query": query, "attempts": 0})
    entry["status"] = status
    entry["last_attempt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    # Only count attempts on genuine failures (no results, all candidates
    # exhausted), not on infrastructure errors (connection, docker, etc.)
    if status == "failed" and not transient:
        entry["attempts"] = entry.get("attempts", 0) + 1
    if username:
        entry["username"] = username
    if remote_filename:
        entry["remote_filename"] = remote_filename
    if local_path:
        entry["local_path"] = local_path
    if search_query:
        entry["search_query"] = search_query
    entry["error"] = error
    entry["progress"] = progress
    entry["total_size"] = total_size
    state[key] = entry
    return state


def is_exhausted(state: dict, query: str) -> bool:
    key = query_hash(query)
    entry = state.get(key)
    if entry is None:
        return False
    return entry.get("attempts", 0) >= RETRY_CAP


def get_retryable_queries(
    state: dict, cooldown_seconds: int = RETRY_COOLDOWN_SECONDS,
) -> list[str]:
    """Return failed queries whose cooldown has elapsed and aren't exhausted."""
    now = time.time()
    retryable = []
    for _key, entry in state.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "failed":
            continue
        if entry.get("attempts", 0) >= RETRY_CAP:
            continue
        last = entry.get("last_attempt", "")
        if last:
            try:
                last_ts = time.mktime(time.strptime(last, "%Y-%m-%dT%H:%M:%S"))
                if now - last_ts < cooldown_seconds:
                    continue
            except ValueError:
                pass
        retryable.append(entry["query"])
    return retryable


def has_pending_work(state: dict) -> bool:
    """Return True if any query is still retryable (not completed/exhausted)."""
    for _key, entry in state.items():
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if status in ("queued", "searching", "downloading", "downloaded"):
            return True
        if status == "failed" and entry.get("attempts", 0) < RETRY_CAP:
            return True
    return False
