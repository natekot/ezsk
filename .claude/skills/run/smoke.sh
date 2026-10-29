#!/usr/bin/env bash
# Launch ezsk (slskd + web UI) and run read-only checks against it.
# Leaves the app running. Stop it with: .claude/skills/run/smoke.sh stop
set -uo pipefail

PORT=8642
SLSKD_PORT=5030
LOG=/tmp/ezsk-run.log
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SKILL_DIR/../../.." && pwd)"

cd "$REPO" || exit 1

stop() {
  lsof -ti:$PORT -sTCP:LISTEN | xargs -r kill 2>/dev/null
  lsof -ti:$SLSKD_PORT -sTCP:LISTEN | xargs -r kill 2>/dev/null
  echo "stopped (ports $PORT, $SLSKD_PORT released)"
}

if [ "${1:-}" = "stop" ]; then stop; exit 0; fi

fail() { echo "FAIL: $*" >&2; echo "--- last 30 lines of $LOG ---" >&2; tail -30 "$LOG" >&2; exit 1; }

# --- launch ---------------------------------------------------------------
if lsof -ti:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "web UI already running on :$PORT"
else
  MANAGED=""
  if lsof -ti:$SLSKD_PORT -sTCP:LISTEN >/dev/null 2>&1; then
    echo "slskd already running on :$SLSKD_PORT -- using it"
  else
    MANAGED="--managed"
    echo "no slskd on :$SLSKD_PORT -- starting a managed one"
  fi

  # EZSK_NO_AUTORUN=1 disables the auto-run download loop.
  # See SKILL.md "Why EZSK_NO_AUTORUN".
  EZSK_NO_AUTORUN=1 \
    uv run ezsk $MANAGED --serve > "$LOG" 2>&1 &

  echo "launching (log: $LOG)"
  for _ in $(seq 1 90); do
    curl -sf "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1 && break
    sleep 1
  done
fi

curl -sf "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1 \
  || fail "web UI never came up on :$PORT"

# --- checks ---------------------------------------------------------------
echo
echo "1. GET / serves the app, not the setup wizard"
BODY=$(curl -sf "http://127.0.0.1:$PORT/")
case "$BODY" in
  *"Setup"*"slskd"*) fail "served setup.html -- config or api_key missing (needs_setup)" ;;
esac
echo "   ok ($(printf '%s' "$BODY" | wc -c | tr -d ' ') bytes)"

echo "2. GET /api/status (counts come from the real state.json)"
curl -sf "http://127.0.0.1:$PORT/api/status" \
  | uv run python -c '
import json,sys
d = json.load(sys.stdin)
print("   counts:", d["counts"])
print("   entries:", len(d.get("entries", [])), "| running:", d.get("running"))
assert d.get("running") is False, "a batch is already running -- expected idle"
' || fail "/api/status check failed"

echo "3. GET /api/config (secrets must be masked)"
curl -sf "http://127.0.0.1:$PORT/api/config" \
  | uv run python -c '
import json,sys
from ezsk.config import Config
d = json.load(sys.stdin)
real = Config.from_file(); real.apply_env()
for section, key in [("slskd", "api_key"), ("anthropic", "api_key")]:
    got = d.get(section, {}).get(key, "")
    actual = getattr(real, "api_key" if section == "slskd" else "anthropic_api_key")
    assert got.startswith("*"), f"{section}.{key} not masked: {got!r}"
    assert got != actual, f"{section}.{key} leaked in full"
print("   ok  secrets masked; soulseek_username:", repr(d["slskd"].get("soulseek_username")))
assert "docker" not in d, "stale [docker] section still emitted"
' || fail "/api/config check failed"

echo "4. slskd health"
API_KEY=$(uv run python -c 'from ezsk.config import Config; c=Config.from_file(); c.apply_env(); print(c.api_key)')
# slskd reports "Connected, LoggingIn" for a few seconds after start, so poll
# until the Soulseek login actually completes rather than sampling once.
STATE=""
for _ in $(seq 1 60); do
  APP=$(curl -sf -H "X-API-Key: $API_KEY" "http://localhost:$SLSKD_PORT/api/v0/application") || { sleep 1; continue; }
  STATE=$(printf '%s' "$APP" | uv run python -c 'import json,sys; print(json.load(sys.stdin).get("server",{}).get("state",""))')
  case "$STATE" in *LoggedIn*) break ;; esac
  sleep 1
done
VER=$(printf '%s' "$APP" | uv run python -c 'import json,sys; print(json.load(sys.stdin).get("version",{}).get("current",""))')
echo "   slskd $VER | server: $STATE"
case "$STATE" in
  *LoggedIn*) ;;
  *) fail "slskd never logged in to Soulseek (last state: ${STATE:-unreachable})" ;;
esac

echo "5. search + ranking (no downloads)"
uv run python "$SKILL_DIR/search_check.py" "${SEARCH_QUERY:-}" 2>&1 | sed 's/^/   /' \
  || fail "search check failed"

echo
echo "App is running: http://127.0.0.1:$PORT   (log: $LOG)"
echo "Stop with: .claude/skills/run/smoke.sh stop"
