---
name: run
description: Launch and drive the ezsk app — starts a managed slskd plus the web UI on :8642 and smoke-tests it. Use when asked to run, start, serve, or screenshot ezsk, or to confirm a change works in the real app.
---

# Running ezsk

ezsk is a Starlette web UI (port **8642**) driving a local **slskd** daemon
(port **5030**). Both must be up. The app runs against the real config at
`~/Library/Application Support/ezsk/config.toml`, the real `state.json`, and the
real music library.

## Setup

```bash
uv pip install -e ".[test,web,verify,ai]"
```

The venv has no `pytest` console script — use `uv run python -m pytest tests/ -q`,
or the `/test` command.

## Run

```bash
./.claude/skills/run/smoke.sh
```

Starts slskd (managed, downloading the binary on first run) if nothing is on
:5030, launches the web UI, waits for readiness, then runs five read-only
checks: the app page renders (not the setup wizard), `/api/status` returns the
real state counts, `/api/config` masks secrets, slskd reports `LoggedIn`, and a
live search ranks candidates. It leaves the app running and prints the URL.

Expected tail of a good run:

```
4. slskd health
   slskd 0.26.0.0 | server: Connected, LoggedIn
5. search + ranking (no downloads)
   responses: 2344 users
   ranked candidates: 6085
      256.5   flac   1541kbps  02 Radiohead - Creep.flac
```

Override the search with `SEARCH_QUERY="Artist Title" ./.claude/skills/run/smoke.sh`.

Then open <http://127.0.0.1:8642> and look at it. Logs: `/tmp/ezsk-run.log`.

## Stop

```bash
./.claude/skills/run/smoke.sh stop
```

## Why EZSK_NO_AUTORUN

The script launches with `EZSK_NO_AUTORUN=1`. **This is a safety interlock, not
noise.** `_auto_run_loop` is spawned in the lifespan hook and runs its first
batch *immediately*. The real config is fully populated, so an unguarded
`--serve` starts downloading into `~/music/soulseek` within seconds, picking up
queued entries from `state.json`, `queued.txt`, and every `failed` entry past
its retry cooldown. `EZSK_NO_AUTORUN=1` makes `autorun_enabled()`
(`ezsk/web/app.py`) return false, so the loop returns immediately while the
rest of the UI still works on the real config — the watch dir and its files
stay visible and uploads still work.

Do **not** substitute `EZSK_WATCH_DIR= ANTHROPIC_API_KEY=` for this. That used
to work by accident and no longer does: `apply_env` now ignores env vars that
are set but empty, so those exports leave the real config intact and the
download loop runs.

To deliberately let it process the queue, run without the prefix:

```bash
uv run ezsk --managed --serve
```

**Do not POST to** `/api/run`, `/api/retry`, `/api/retry-one`, `/api/replace`,
or `/api/files/run-all` while smoke-testing — they start real downloads against
the real library. Every check in `smoke.sh` is a GET.

## Other modes

| Command | What it does |
|---|---|
| `uv run ezsk --desktop` | slskd + web UI + browser + tray (what a packaged user gets) |
| `uv run ezsk -f tracks.txt` | CLI batch — **real downloads** |
| `uv run ezsk "Artist - Title"` | single track — **real download** |
| `uv run ezsk --status` | regenerate the static HTML dashboard and open it |
| `uv run ezsk --setup` | interactive first-run wizard (rewrites `config.toml`) |

## Troubleshooting

- **`GET /` returns the setup wizard** — `needs_setup` is true (`app.py:43`):
  `config.toml` is missing or `api_key` is empty.
- **slskd exits immediately with code 0** — check
  `~/Library/Application Support/ezsk/slskd/slskd.log`. If it names a missing
  `wwwroot`, the install in `~/Library/Application Support/ezsk/bin/` is
  incomplete; delete `bin/version.txt` and rerun to force a clean re-extract.
- **Search returns 0 responses** — a freshly started slskd needs a few seconds
  to join the distributed network. `search_check.py` already retries 3 times.
- **Port already in use** — `smoke.sh stop`, or
  `lsof -ti:8642 -sTCP:LISTEN | xargs kill`.
- **Never run two slskd instances at once** — they contend for Soulseek listen
  port 50300 and the same account.
