from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager

from ezsk.normalize import query_hash
from ezsk.paths import atomic_write_text, default_state_path

RETRY_CAP = 5
RETRY_COOLDOWN_SECONDS = 86400  # 24 hours

# state.json is rewritten whole on every update, so an unsynchronized
# load -> mutate -> save triple silently drops one writer's keys. Several
# threads do exactly that concurrently: the four download workers, the search
# stage, and the on-demand tasks POST /api/run spawns. Serialize the whole
# transaction, not just the write.
#
# Reentrant because state_txn() calls load_state()/save_state(), which take it
# too, and because load_state() can re-enter save_state() to persist a key
# migration.
_state_lock = threading.RLock()

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
    with _state_lock:
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


def _write_state(path: str, state: dict) -> None:
    with _state_lock:
        atomic_write_text(path, json.dumps(state, indent=2))


def _notify(state: dict) -> None:
    """Fan the change out to SSE subscribers. Never call this holding the lock.

    The web layer's callback rebuilds the whole status payload, which re-reads
    state.json -- running that under the lock would serialize every writer in
    the pipeline behind it.
    """
    for cb in list(_on_change):
        try:
            cb(state)
        except Exception:
            pass


def save_state(path: str, state: dict) -> None:
    _write_state(path, state)
    _notify(state)


@contextmanager
def state_lock():
    """Hold the state lock without implying a save.

    For callers that decide mid-transaction whether there is anything to write
    -- an unconditional save would cost a file rewrite and an SSE fan-out on
    every no-op. Call load_state()/save_state() inside the block as needed.
    """
    with _state_lock:
        yield


@contextmanager
def state_txn(path: str):
    """Hold the state lock across a load -> mutate -> save cycle.

    Yields the loaded state; whatever the body mutates is saved on exit. Use
    this instead of a bare load_state()/save_state() pair anywhere that runs on
    a worker thread, or a concurrent writer's keys get clobbered.
    """
    with _state_lock:
        state = load_state(path)
        yield state
        _write_state(path, state)
    # Outside the lock -- see _notify().
    _notify(state)


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
    # exhausted), not on infrastructure errors (connection, slskd down, etc.)
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


def retry_due(
    entry: dict, now: float | None = None,
    cooldown_seconds: int = RETRY_COOLDOWN_SECONDS,
) -> bool:
    """Whether a failed entry may be searched again: under the cap, cooldown up."""
    if entry.get("status") != "failed":
        return False
    if entry.get("attempts", 0) >= RETRY_CAP:
        return False
    last = entry.get("last_attempt", "")
    if not last:
        return True
    try:
        last_ts = time.mktime(time.strptime(last, "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return True
    return (time.time() if now is None else now) - last_ts >= cooldown_seconds


def get_retryable_queries(
    state: dict, cooldown_seconds: int = RETRY_COOLDOWN_SECONDS,
) -> list[str]:
    """Return failed queries whose cooldown has elapsed and aren't exhausted."""
    now = time.time()
    return [
        entry["query"] for entry in state.values()
        if isinstance(entry, dict) and retry_due(entry, now, cooldown_seconds)
    ]


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
