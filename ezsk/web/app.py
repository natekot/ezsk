from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterable
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ezsk.config import Config
from ezsk.display import print_error, print_status
from ezsk.service import DownloadService
from ezsk.download import _load_queue_lines
from ezsk.normalize import query_hash
from ezsk.state import (
    get_retryable_queries,
    is_completed,
    is_exhausted,
    load_state,
    register_on_change,
    retry_due,
    unregister_on_change,
)
from ezsk.watch import WatchDirService

STATIC_DIR = Path(__file__).parent / "static"
POLL_INTERVAL_SECONDS = 300  # 5 minutes between retry rounds


class AppState:
    """Shared state between routes and SSE."""

    def __init__(self, config: Config):
        self.config = config
        self.service = DownloadService(config)
        self.watch = WatchDirService(
            config.watch_dir or "", config.anthropic_api_key,
        )
        self._subscribers: list[asyncio.Queue] = []
        self._loop: asyncio.AbstractEventLoop | None = None

        # Setup wizard state
        from ezsk.config import DEFAULT_CONFIG_PATH
        self.needs_setup = not DEFAULT_CONFIG_PATH.exists() or not config.api_key
        self._setup_status: str = ""
        self._setup_error: str | None = None
        self._setup_done: bool = False

        # Auto-update checker
        from ezsk.updater import UpdateChecker
        self.update_checker = UpdateChecker(verbose=config.verbose)
        self.update_checker.start()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _on_state_change(self, state: dict) -> None:
        """Called from worker thread when state changes."""
        if self._loop is None:
            return
        data = json.dumps(self._build_status(state))
        for q in list(self._subscribers):
            self._loop.call_soon_threadsafe(q.put_nowait, ("state", data))

    def _build_status(self, state: dict | None = None) -> dict:
        status = self.service.get_status()
        status["running"] = self.service.running
        return status

    def notify_running(self, running: bool) -> None:
        if self._loop is None:
            return
        data = json.dumps({"running": running})
        for q in list(self._subscribers):
            self._loop.call_soon_threadsafe(q.put_nowait, ("running", data))

    def files_payload(self) -> dict:
        """The watch-dir snapshot pushed to clients.

        Carries watch_dir so the UI can distinguish "no watch dir configured"
        from "watch dir is empty".
        """
        return {
            "files": [f.to_dict() for f in self.watch.list_files()],
            "watch_dir": self.config.watch_dir,
        }

    def notify_files(self) -> None:
        if self._loop is None:
            return
        data = json.dumps(self.files_payload())
        for q in list(self._subscribers):
            self._loop.call_soon_threadsafe(q.put_nowait, ("files", data))


def autorun_enabled(config: Config) -> bool:
    """Whether the web app should continuously process the watch dir.

    Needs a watch dir and an Anthropic key to have anything to do, and stays
    off when no_autorun is set (used by the smoke test to keep it from
    starting real downloads).
    """
    return bool(config.watch_dir and config.anthropic_api_key and not config.no_autorun)


def gather_autorun_queries(
    watch_queries: list[str],
    state: dict,
    queue_lines: Iterable[str],
    retryable: list[str],
) -> list[str]:
    """What one pass of the auto-run loop hands to run_batch.

    Draws on the watch dir, orphaned "queued" entries in state.json,
    queued.txt, and failed queries whose cooldown is up, then drops whatever
    has nothing left to do: completed, exhausted, or failed and still inside
    its retry cooldown. run_batch writes every miss to queued.txt and the
    watch dir lists every query ever parsed, so without that last check a
    track nobody has is searched every pass and burns its whole retry cap in
    half an hour.
    """
    orphaned = [
        e["query"] for e in state.values()
        if isinstance(e, dict) and e.get("status") == "queued" and e.get("query")
    ]
    gathered: list[str] = []
    seen: set[str] = set()
    for q in [*watch_queries, *orphaned, *sorted(queue_lines), *retryable]:
        if q in seen:
            continue
        seen.add(q)
        if is_completed(state, q) or is_exhausted(state, q):
            continue
        entry = state.get(query_hash(q))
        if isinstance(entry, dict) and entry.get("status") == "failed" and not retry_due(entry):
            continue
        gathered.append(q)
    return gathered


def create_app(config: Config) -> Starlette:
    from ezsk.web.api import routes

    app_state = AppState(config)

    async def _auto_run_loop():
        """Continuously process watch dir files and retry failed queries.

        Runs until cancelled. A pass with nothing to do starts no batch and
        logs nothing, so files added to the watch dir later are still picked
        up within POLL_INTERVAL_SECONDS.
        """
        if not autorun_enabled(config):
            return

        queue_path = os.path.join(config.output_dir, "queued.txt")
        while True:
            try:
                watch_queries = await asyncio.to_thread(
                    app_state.watch.collect_all_queries,
                )
                app_state.notify_files()

                state = await asyncio.to_thread(
                    load_state, config.state_file,
                )

                retryable = get_retryable_queries(state)
                if retryable:
                    print_status(
                        f"Retrying {len(retryable)} failed queries "
                        f"(cooldown elapsed)",
                    )
                queries = gather_autorun_queries(
                    watch_queries, state, _load_queue_lines(queue_path), retryable,
                )

                if queries:
                    app_state.notify_running(True)
                    try:
                        await asyncio.to_thread(
                            app_state.service.run_batch, queries,
                        )
                    finally:
                        app_state.notify_running(False)
            except Exception as e:
                print_error(f"Auto-run failed: {e}")
                app_state.notify_running(False)

            if app_state.service._cancelled:
                break

            # Sleep in 1-second increments so Cancel stays responsive
            for _ in range(POLL_INTERVAL_SECONDS):
                if app_state.service._cancelled:
                    break
                await asyncio.sleep(1)

            if app_state.service._cancelled:
                break

    @asynccontextmanager
    async def lifespan(app):
        app_state._loop = asyncio.get_running_loop()
        register_on_change(app_state._on_state_change)
        _startup_task = asyncio.create_task(_auto_run_loop())
        try:
            yield
        finally:
            unregister_on_change(app_state._on_state_change)
            app_state._loop = None

    app = Starlette(
        routes=[
            *routes(app_state),
            Mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static"),
        ],
        lifespan=lifespan,
    )
    app.state.app_state = app_state
    return app


def run_server(config: Config, port: int = 8642) -> None:
    import uvicorn

    from ezsk.display import print_status

    app = create_app(config)
    print_status(f"Serving at http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
