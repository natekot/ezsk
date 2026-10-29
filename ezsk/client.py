from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import requests
import slskd_api

from ezsk.config import Config
from ezsk.display import print_status


class SearchRejected(Exception):
    """slskd refused the search text itself.

    Retrying the same text can't succeed, so unlike a 409 or a 429 this is a
    real miss: callers charge an attempt for it rather than treating it as an
    infrastructure blip.
    """


def searchable(text: str) -> bool:
    """Whether slskd will accept `text` as a search at all.

    Soulseek.NET refuses (slskd answers 400) a search with no word longer than
    one character, not counting exclusions -- words starting with "-". Titles
    like "I.D.L.E." normalize to exactly that: "i d l e".
    """
    return any(len(w) > 1 and not w.startswith("-") for w in text.split())

# Searches this process currently has open on slskd. Tracked per process, not
# per client, because several SlskdClient instances are live at once: run_batch
# shares one across the search stage, and every download_candidates() call
# builds its own. cleanup_searches() deletes by enumerating the server, so
# without this it would delete another thread's in-flight search and leave its
# owner to record a false miss.
_active_searches: set[str] = set()
_active_lock = threading.Lock()

# Reconnecting bounces the one Soulseek session the whole process shares, so
# only one thread may do it. Without this, two search workers that hit a 409
# at the same moment both call ensure_connected(), and the second one's
# disconnect/reconnect lands on the session the first just rebuilt.
_reconnect_lock = threading.RLock()


def active_search_count() -> int:
    """How many searches this process has open on slskd right now."""
    with _active_lock:
        return len(_active_searches)


class SearchGate:
    """Process-wide cap on concurrent searches.

    slskd forwards only two searches at a time to the Soulseek server and
    queues the rest internally, and the server bans for ~30 minutes if you fire
    roughly 30 in quick succession. Going past two buys no throughput and costs
    accuracy: a search waiting in slskd's queue still burns the local deadline
    in _smart_search, comes back empty, and is recorded as a real miss -- which
    counts against the query's retry cap.

    Every search funnels through _smart_search, so this single gate covers the
    batch's search workers, the per-query tasks POST /api/run spawns, retry and
    replace alike. Resizable because the web UI can change the limit at
    runtime.
    """

    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._active = 0
        self._cond = threading.Condition()

    @property
    def limit(self) -> int:
        with self._cond:
            return self._limit

    def set_limit(self, limit: int) -> None:
        limit = max(1, limit)
        with self._cond:
            if limit != self._limit:
                self._limit = limit
                self._cond.notify_all()

    @contextmanager
    def hold(self):
        with self._cond:
            while self._active >= self._limit:
                self._cond.wait()
            self._active += 1
        try:
            yield
        finally:
            with self._cond:
                self._active -= 1
                self._cond.notify()


search_gate = SearchGate(2)


class SlskdClient:
    def __init__(self, config: Config):
        self.config = config
        self._client = slskd_api.SlskdClient(
            host=config.url,
            api_key=config.api_key,
            timeout=30,
        )

    def check_connection(self) -> None:
        state = self._client.application.state()
        server = state.get("server", {})
        if not server.get("isConnected") or not server.get("isLoggedIn"):
            raise ConnectionError(
                "slskd is running but not connected to Soulseek. "
                "Make sure no other Soulseek client is using the same account."
            )

    def _poll_connected(self, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(3)
            try:
                state = self._client.application.state()
                server = state.get("server", {})
                if server.get("isConnected") and server.get("isLoggedIn"):
                    return True
            except Exception:
                pass
        return False

    def _is_connected(self) -> bool:
        try:
            server = self._client.application.state().get("server", {})
        except Exception:
            return False
        return bool(server.get("isConnected") and server.get("isLoggedIn"))

    def ensure_connected(self) -> None:
        if self._is_connected():
            return

        with _reconnect_lock:
            # Re-check: while we waited for the lock another worker may have
            # already brought the session back, and bouncing a healthy
            # connection would knock its search off the network.
            if self._is_connected():
                return
            self._reconnect()

    def _reconnect(self) -> None:
        # Ask slskd to reconnect via its API
        print_status("slskd disconnected, requesting reconnect...")
        try:
            self._client.server.connect()
        except Exception:
            pass

        if self._poll_connected(30):
            print_status("slskd reconnected")
            return

        # Last resort: disconnect then reconnect to reset state
        print_status("Retrying with disconnect/reconnect...")
        try:
            self._client.server.disconnect()
            time.sleep(2)
            self._client.server.connect()
        except Exception:
            pass

        if self._poll_connected(15):
            print_status("slskd reconnected")
            return

        raise ConnectionError(
            "slskd is not connected to Soulseek after reconnect attempts. "
            "Check the slskd container or restart it manually."
        )

    def cleanup_searches(self, spare_active: bool = True) -> int:
        """Delete searches left on the slskd instance. Returns how many went.

        slskd's search history is bounded and a full one may make
        start_search fail with a 400, so this doubles as the recovery path
        from that. It works by enumerating the server, which also turns up
        searches other threads have in flight right now -- deleting one of
        those makes its owner see zero responses and record a false miss.
        Pass spare_active=False only from a cold start, where none of ours
        are running.
        """
        try:
            searches = self._client.searches.get_all()
        except Exception:
            return 0
        spare = set(_active_searches) if spare_active else set()
        deleted = 0
        for s in searches:
            if s["id"] in spare:
                continue
            try:
                self._client.searches.delete(s["id"])
            except Exception:
                continue
            deleted += 1
        return deleted

    def start_search(self, query: str, timeout_ms: int = 60000) -> str:
        if not searchable(query):
            raise SearchRejected(
                f"slskd needs a word longer than one character to search, got {query!r}"
            )
        try:
            result = self._client.searches.search_text(
                searchText=query,
                searchTimeout=timeout_ms,
                responseLimit=10000,
            )
        except requests.HTTPError as e:
            # slskd_api raises via raise_for_status(), whose message is just
            # the status and URL. slskd puts the reason in the body; keep it,
            # after the status prefix the callers' retry handling matches on.
            body = e.response.text.strip().strip('"')[:300] if e.response is not None else ""
            if not body:
                raise
            raise requests.HTTPError(f"{e}: {body}", response=e.response) from e
        search_id = result["id"]
        with _active_lock:
            _active_searches.add(search_id)
        return search_id

    def search_state(self, search_id: str) -> dict:
        return self._client.searches.state(search_id)

    def get_search_responses(self, search_id: str) -> list[dict]:
        return self._client.searches.search_responses(search_id)

    def stop_search(self, search_id: str) -> None:
        try:
            self._client.searches.stop(search_id)
        except Exception:
            pass

    def delete_search(self, search_id: str) -> None:
        try:
            self._client.searches.delete(search_id)
        except Exception:
            pass
        finally:
            with _active_lock:
                _active_searches.discard(search_id)

    def search(self, query: str, timeout_ms: int = 60000) -> tuple[str, list[dict]]:
        """Simple search that waits for completion. Used by _search_with_retry fallback."""
        search_id = self.start_search(query, timeout_ms)
        deadline = time.time() + (timeout_ms / 1000) + 10
        while time.time() < deadline:
            state = self.search_state(search_id)
            if state["isComplete"]:
                break
            time.sleep(2)
        else:
            # A busy query keeps receiving results, so slskd pushes its search
            # timeout out and the search is still running when we give up on
            # it. Stop it explicitly -- slskd serves no responses for an active
            # search, and needs a moment after the stop before they appear.
            self.stop_search(search_id)

        responses = []
        for _ in range(15):
            try:
                responses = self.get_search_responses(search_id)
            except Exception:
                break
            if responses:
                break
            time.sleep(0.2)
        self.delete_search(search_id)
        return search_id, responses

    def enqueue_download(self, username: str, filename: str, size: int) -> None:
        self._client.transfers.enqueue(username, [{"filename": filename, "size": size}])

    def get_downloads(self, username: str) -> dict:
        return self._client.transfers.get_downloads(username)

    def get_all_downloads(self) -> list[dict]:
        return self._client.transfers.get_all_downloads()

    def wait_for_download(
        self, username: str, filename: str, timeout: int = 300,
        on_progress: "Callable[[int, int], None] | None" = None,
        stall_timeout: int = 60,
    ) -> dict | None:
        deadline = time.time() + timeout
        last_bytes = -1
        last_progress_time = time.time()
        while time.time() < deadline:
            try:
                transfer = self.get_downloads(username)
            except Exception:
                time.sleep(2)
                continue
            for directory in transfer.get("directories", []):
                for f in directory.get("files", []):
                    if f["filename"] == filename:
                        transferred = f.get("bytesTransferred", 0)
                        if on_progress:
                            on_progress(transferred, f.get("size", 0))
                        state = f["state"]
                        if "Completed" in state and "Succeeded" in state:
                            return f
                        if "Completed" in state and ("Errored" in state or "Cancelled" in state):
                            return None
                        # Stall detection: no progress for stall_timeout seconds
                        if transferred != last_bytes:
                            last_bytes = transferred
                            last_progress_time = time.time()
                        elif time.time() - last_progress_time > stall_timeout:
                            return None
            time.sleep(2)
        return None
