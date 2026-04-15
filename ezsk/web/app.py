from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ezsk.config import Config
from ezsk.display import print_error, print_status
from ezsk.service import DownloadService
from ezsk.download import _load_queue_lines
from ezsk.state import (
    get_retryable_queries,
    has_pending_work,
    load_state,
    register_on_change,
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

    def notify_files(self) -> None:
        if self._loop is None:
            return
        files = [f.to_dict() for f in self.watch.list_files()]
        data = json.dumps({"files": files})
        for q in list(self._subscribers):
            self._loop.call_soon_threadsafe(q.put_nowait, ("files", data))


def create_app(config: Config) -> Starlette:
    from ezsk.web.api import routes

    app_state = AppState(config)

    async def _auto_run_loop():
        """Continuously process watch dir files and retry failed queries."""
        if not config.watch_dir or not config.anthropic_api_key:
            return

        first_run = True
        while True:
            try:
                queries = await asyncio.to_thread(
                    app_state.watch.collect_all_queries,
                )
                app_state.notify_files()

                state = await asyncio.to_thread(
                    load_state, config.state_file,
                )

                # Pick up orphaned queued entries from state.json and queued.txt
                seen = set(queries)
                for _key, entry in state.items():
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("status") == "queued":
                        q = entry.get("query", "")
                        if q and q not in seen:
                            queries.append(q)
                            seen.add(q)
                import os
                queue_path = os.path.join(config.output_dir, "queued.txt")
                for q in _load_queue_lines(queue_path):
                    if q not in seen:
                        queries.append(q)
                        seen.add(q)

                if not first_run:
                    retryable = get_retryable_queries(state)
                    if retryable:
                        print_status(
                            f"Retrying {len(retryable)} failed queries "
                            f"(cooldown elapsed)",
                        )
                    for q in retryable:
                        if q not in seen:
                            queries.append(q)
                            seen.add(q)

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

            first_run = False

            if app_state.service._cancelled:
                break

            state = await asyncio.to_thread(load_state, config.state_file)
            if not has_pending_work(state):
                print_status("All queries completed or exhausted.")
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
