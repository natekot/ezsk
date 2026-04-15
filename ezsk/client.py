from __future__ import annotations

import time

import slskd_api

from ezsk.config import Config
from ezsk.display import print_status


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

    def ensure_connected(self) -> None:
        state = self._client.application.state()
        server = state.get("server", {})
        if server.get("isConnected") and server.get("isLoggedIn"):
            return

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

    def cleanup_searches(self) -> int:
        try:
            searches = self._client.searches.get_all()
            for s in searches:
                self._client.searches.delete(s["id"])
            return len(searches)
        except Exception:
            return 0

    def start_search(self, query: str, timeout_ms: int = 60000) -> str:
        result = self._client.searches.search_text(
            searchText=query,
            searchTimeout=timeout_ms,
            responseLimit=10000,
        )
        return result["id"]

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
            self.stop_search(search_id)
        responses = self.get_search_responses(search_id)
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
