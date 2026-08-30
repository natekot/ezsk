from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

if TYPE_CHECKING:
    from ezsk.web.app import AppState

STATIC_DIR = Path(__file__).parent / "static"

# Keep references to background tasks so they don't get GC'd
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def index(request: Request) -> FileResponse:
    app_state: AppState = request.app.state.app_state
    if app_state.needs_setup:
        return FileResponse(str(STATIC_DIR / "setup.html"))
    return FileResponse(str(STATIC_DIR / "index.html"))


async def get_status(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state
    status = app_state.service.get_status()
    status["running"] = app_state.service.running
    return JSONResponse(status)


async def get_config(request: Request) -> JSONResponse:
    from ezsk.config import Config

    app_state: AppState = request.app.state.app_state
    data = app_state.config.to_dict()
    for section in data.values():
        for key in list(section):
            if key in Config.SENSITIVE_FIELDS:
                val = section[key]
                section[key] = ("*" * 8 + val[-4:]) if len(val) > 4 else "****"
    return JSONResponse(data)


async def put_config(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state
    body = await request.json()

    # Don't allow overwriting sensitive fields with masked values
    for section in body.values():
        if not isinstance(section, dict):
            continue
        for key in list(section):
            if key in ("api_key", "anthropic_api_key"):
                if section[key].startswith("****"):
                    del section[key]

    app_state.config.update_from_dict(body)
    app_state.config.save()
    return JSONResponse({"ok": True})


async def post_run(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state

    body = await request.json()
    queries = body.get("queries", [])

    if isinstance(queries, str):
        queries = [q.strip() for q in queries.splitlines() if q.strip()]

    if not queries:
        return JSONResponse({"error": "No queries provided"}, status_code=400)

    import os
    from ezsk.download import _append_to_queue
    from ezsk.state import load_state, is_completed

    # Filter already-completed queries.
    state = await asyncio.to_thread(load_state, app_state.config.state_file)
    new_queries = [q for q in queries if not is_completed(state, q)]
    if not new_queries:
        return JSONResponse({"ok": True, "count": 0})

    # Persist immediately so queries appear in the UI before the batch starts.
    queue_path = os.path.join(app_state.config.output_dir, "queued.txt")
    await asyncio.to_thread(_append_to_queue, queue_path, new_queries)

    # Push state update directly to SSE subscribers (we're already in the loop).
    status = app_state.service.get_status()
    status["running"] = app_state.service.running
    data = json.dumps(status)
    for q in list(app_state._subscribers):
        try:
            q.put_nowait(("state", data))
        except Exception:
            pass

    # Spawn each query as an independent on-demand task so it runs immediately
    # regardless of whether a background batch is already in progress.
    async def _run_on_demand(query: str) -> None:
        await asyncio.to_thread(app_state.service.run_single, query)

    for q in new_queries:
        _spawn(_run_on_demand(q))

    return JSONResponse({"ok": True, "count": len(new_queries)})


async def post_retry_one(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state

    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        return JSONResponse({"error": "No query provided"}, status_code=400)

    async def _run() -> None:
        await asyncio.to_thread(app_state.service.retry_one, query)

    _spawn(_run())
    return JSONResponse({"ok": True})


async def post_replace(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state

    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        return JSONResponse({"error": "No query provided"}, status_code=400)
    # Optional edited search string; defaults to the original query.
    new_query = (body.get("new_query") or "").strip() or query

    # Run on-demand so a track can be replaced even while a batch is in progress.
    # replace() resets the entry and save_state() fans the update out to SSE
    # subscribers, so the row reflects the pending re-download on its own.
    async def _run_replace() -> None:
        await asyncio.to_thread(app_state.service.replace, query, new_query)

    _spawn(_run_replace())
    return JSONResponse({"ok": True})


async def post_retry(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state
    if app_state.service.running:
        return JSONResponse({"error": "Batch already running"}, status_code=409)

    queries = app_state.service.retry_failed()
    if not queries:
        return JSONResponse({"ok": True, "count": 0})

    async def _run() -> None:
        app_state.notify_running(True)
        try:
            await asyncio.to_thread(app_state.service.run_batch, queries)
        finally:
            app_state.notify_running(False)

    _spawn(_run())
    return JSONResponse({"ok": True, "count": len(queries)})


async def post_cancel(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state
    app_state.service.cancel()
    return JSONResponse({"ok": True})


async def events(request: Request) -> None:
    from sse_starlette.sse import EventSourceResponse

    app_state: AppState = request.app.state.app_state
    queue = app_state.subscribe()

    async def event_generator():
        try:
            # Send initial state + files
            status = app_state.service.get_status()
            status["running"] = app_state.service.running
            yield {"event": "state", "data": json.dumps(status)}
            files = [f.to_dict() for f in app_state.watch.list_files()]
            yield {"event": "files", "data": json.dumps({"files": files})}

            while True:
                try:
                    event_type, data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {"event": event_type, "data": data}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            app_state.unsubscribe(queue)

    return EventSourceResponse(event_generator())


async def get_files(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state
    files = [f.to_dict() for f in app_state.watch.list_files()]
    return JSONResponse({
        "files": files,
        "watch_dir": app_state.config.watch_dir,
        "has_api_key": bool(app_state.config.anthropic_api_key),
    })


async def upload_files(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state
    if not app_state.config.watch_dir:
        return JSONResponse({"error": "Watch dir not configured"}, status_code=400)
    if not app_state.config.anthropic_api_key:
        return JSONResponse({"error": "Anthropic API key not configured"}, status_code=400)

    form = await request.form()
    uploaded = []
    for key in form:
        upload = form[key]
        if not hasattr(upload, "read"):
            continue
        name = PurePath(upload.filename or "upload.txt").name
        if not name or name.startswith("."):
            continue
        data = await upload.read()
        app_state.watch.save_uploaded(name, data)
        uploaded.append(name)

    if not uploaded:
        return JSONResponse({"error": "No files uploaded"}, status_code=400)

    # Parse each file in background
    async def _parse_all():
        for name in uploaded:
            try:
                await asyncio.to_thread(app_state.watch.parse_file, name)
            except Exception as e:
                logging.getLogger(__name__).warning("parse failed for %s: %s", name, e)
            app_state.notify_files()

    app_state.notify_files()
    _spawn(_parse_all())
    return JSONResponse({"ok": True, "uploaded": uploaded})


async def parse_file(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state
    name = request.path_params["name"]
    if not app_state.config.anthropic_api_key:
        return JSONResponse({"error": "Anthropic API key not configured"}, status_code=400)

    async def _parse():
        try:
            await asyncio.to_thread(app_state.watch.parse_file, name)
        except Exception:
            pass
        app_state.notify_files()

    app_state.notify_files()
    _spawn(_parse())
    return JSONResponse({"ok": True})


async def run_file(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state
    if app_state.service.running:
        return JSONResponse({"error": "Batch already running"}, status_code=409)

    name = request.path_params["name"]
    queries = app_state.watch.read_cached(name)
    if not queries:
        return JSONResponse({"error": "No cached queries for this file"}, status_code=400)

    async def _run():
        app_state.notify_running(True)
        try:
            await asyncio.to_thread(app_state.service.run_batch, queries)
        finally:
            app_state.notify_running(False)

    _spawn(_run())
    return JSONResponse({"ok": True, "count": len(queries)})


async def delete_file(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state
    name = request.path_params["name"]
    app_state.watch.remove_file(name)
    app_state.notify_files()
    return JSONResponse({"ok": True})


async def check_file(request: Request) -> JSONResponse:
    """Return which queries from the cached file are already in the library."""
    app_state: AppState = request.app.state.app_state
    name = request.path_params["name"]
    queries = app_state.watch.read_cached(name)
    if not queries:
        return JSONResponse({"error": "No cached queries for this file"}, status_code=400)

    from ezsk.index import check_queries

    index = app_state.config.index or {}
    have, missing = await asyncio.to_thread(check_queries, index, queries)
    return JSONResponse({
        "have": [{"query": q, "path": p} for q, p in have],
        "missing": missing,
    })


async def post_library_rescan(request: Request) -> JSONResponse:
    """Rebuild the output-directory index and refresh it on the live config."""
    app_state: AppState = request.app.state.app_state

    from ezsk.index import build_index, save_index

    def _rebuild() -> int:
        index = build_index(app_state.config.output_dir)
        save_index(app_state.config.output_dir, index)
        app_state.config.index = index
        return len(index)

    count = await asyncio.to_thread(_rebuild)
    return JSONResponse({"ok": True, "count": count})


async def run_all_files(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state
    if app_state.service.running:
        return JSONResponse({"error": "Batch already running"}, status_code=409)
    if not app_state.config.anthropic_api_key:
        return JSONResponse({"error": "Anthropic API key not configured"}, status_code=400)

    async def _run():
        app_state.notify_running(True)
        try:
            queries = await asyncio.to_thread(app_state.watch.collect_all_queries)
            app_state.notify_files()
            if queries:
                await asyncio.to_thread(app_state.service.run_batch, queries)
        finally:
            app_state.notify_running(False)

    _spawn(_run())
    return JSONResponse({"ok": True})


async def post_setup(request: Request) -> JSONResponse:
    """Handle the setup wizard submission."""
    app_state: AppState = request.app.state.app_state
    body = await request.json()
    mode = body.get("mode", "managed")

    import secrets
    from ezsk.config import Config, DEFAULT_CONFIG_PATH
    from ezsk.paths import default_downloads_dir

    config = app_state.config

    if mode == "managed":
        username = body.get("soulseek_username", "")
        password = body.get("soulseek_password", "")
        if not username or not password:
            return JSONResponse({"error": "Soulseek username and password are required"}, status_code=400)

        config.api_key = secrets.token_urlsafe(32)
        config.downloads_dir = str(default_downloads_dir())

        # Store credentials for slskd config generation
        app_state._setup_slsk_username = username
        app_state._setup_slsk_password = password
    else:
        config.url = body.get("url", "http://localhost:5030")
        config.api_key = body.get("api_key", "")
        config.downloads_dir = body.get("downloads_dir", "")
        if not config.api_key:
            return JSONResponse({"error": "API key is required"}, status_code=400)

    output_dir = body.get("output_dir", "")
    if output_dir:
        config.output_dir = output_dir

    fmt = body.get("format", "flac")
    if fmt and fmt != "any":
        config.preferred_format = fmt

    config.verify = True

    # Save config
    config.save()
    app_state._setup_status = "Config saved."
    app_state._setup_error = None
    app_state._setup_done = False

    # Do the slow work (downloading slskd, etc.) in background
    async def _finish_setup():
        try:
            if mode == "managed":
                app_state._setup_status = "Downloading Soulseek connector..."
                from ezsk.slskd_manager import download_slskd, generate_config, ensure_running
                await asyncio.to_thread(download_slskd, True)

                app_state._setup_status = "Configuring your account..."
                await asyncio.to_thread(
                    generate_config, config,
                    app_state._setup_slsk_username,
                    app_state._setup_slsk_password,
                )

                app_state._setup_status = "Connecting to the Soulseek network..."
                await asyncio.to_thread(ensure_running, config)

            app_state._setup_status = "Almost ready..."
            app_state._setup_done = True
            app_state.needs_setup = False
        except Exception as e:
            app_state._setup_error = str(e)

    _spawn(_finish_setup())
    return JSONResponse({"ok": True, "status": "Setting up..."})


async def get_update(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state
    return JSONResponse(app_state.update_checker.to_dict())


async def get_setup_status(request: Request) -> JSONResponse:
    app_state: AppState = request.app.state.app_state
    result: dict = {}
    if getattr(app_state, "_setup_done", False):
        result["done"] = True
    elif getattr(app_state, "_setup_error", None):
        result["error"] = app_state._setup_error
    else:
        result["status"] = getattr(app_state, "_setup_status", "Working...")
    return JSONResponse(result)


def routes(app_state: AppState) -> list[Route]:
    return [
        Route("/", index),
        Route("/api/status", get_status),
        Route("/api/config", get_config, methods=["GET"]),
        Route("/api/config", put_config, methods=["PUT"]),
        Route("/api/run", post_run, methods=["POST"]),
        Route("/api/retry", post_retry, methods=["POST"]),
        Route("/api/replace", post_replace, methods=["POST"]),
        Route("/api/retry-one", post_retry_one, methods=["POST"]),
        Route("/api/cancel", post_cancel, methods=["POST"]),
        Route("/api/events", events),
        Route("/api/files", get_files, methods=["GET"]),
        Route("/api/files/upload", upload_files, methods=["POST"]),
        Route("/api/files/run-all", run_all_files, methods=["POST"]),
        Route("/api/files/{name:path}/parse", parse_file, methods=["POST"]),
        Route("/api/files/{name:path}/run", run_file, methods=["POST"]),
        Route("/api/files/{name:path}/check", check_file, methods=["GET"]),
        Route("/api/files/{name:path}", delete_file, methods=["DELETE"]),
        Route("/api/library/rescan", post_library_rescan, methods=["POST"]),
        Route("/api/setup", post_setup, methods=["POST"]),
        Route("/api/setup/status", get_setup_status, methods=["GET"]),
        Route("/api/update", get_update, methods=["GET"]),
    ]
