#!/usr/bin/env bash
#
# Run one full inspection pass unattended, and survive the terminal closing.
#
#   scripts/overnight.sh                 # every enabled source
#   scripts/overnight.sh --source-type browser
#   scripts/overnight.sh --alerts        # also send the digest at the end
#
# Everything is checked before anything is launched, because the failure worth
# avoiding is discovering at breakfast that nothing ran.

set -euo pipefail

# Always work from the repository root, whatever directory this was called from.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ACIDE="$ROOT/backend/.venv/bin/acide"
LOG_DIR="$ROOT/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/overnight-$STAMP.log"
PIDFILE="$LOG_DIR/overnight.pid"

fail() { printf '\nerror: %s\n' "$1" >&2; exit 1; }

# -- checks ------------------------------------------------------------------
[ -x "$ACIDE" ] || fail "no acide at $ACIDE
  run: python -m venv backend/.venv && backend/.venv/bin/pip install -e 'backend/[browser]'"

[ -f data/setup.json ] || fail "no data/setup.json — configure the portal first"

ENABLED="$("$ROOT/backend/.venv/bin/python" - <<'PY'
import json, pathlib, sys
try:
    config = json.loads(pathlib.Path("data/setup.json").read_text("utf-8"))
except Exception as exc:
    print(f"unreadable:{exc}", end="")
    sys.exit(0)
targets = [t for t in config.get("targets", []) if t.get("enabled", True)]
kinds = {}
for target in targets:
    kinds[target.get("source_type", "?")] = kinds.get(target.get("source_type", "?"), 0) + 1
terms = len((config.get("spider") or {}).get("search_terms") or [])
print(f"{len(targets)}|{kinds.get('browser', 0)}|{terms}|"
      + ",".join(f"{n} {k}" for k, n in sorted(kinds.items())), end="")
PY
)"
case "$ENABLED" in
  unreachable:*|unreadable:*) fail "could not read data/setup.json: ${ENABLED#*:}" ;;
esac
IFS='|' read -r TARGETS RENDERED TERMS BREAKDOWN <<< "$ENABLED"
[ "${TARGETS:-0}" -gt 0 ] || fail "no enabled targets in data/setup.json"

if [ "$RENDERED" -gt 0 ]; then
  "$ROOT/backend/.venv/bin/python" -c "import playwright" 2>/dev/null || fail \
"$RENDERED target(s) render a browser, but Playwright is not installed
  run: backend/.venv/bin/pip install -e 'backend/[browser]' && backend/.venv/bin/playwright install chromium"
fi

mkdir -p "$LOG_DIR"
: > "$LOG" || fail "cannot write $LOG"

# -- report, then launch -----------------------------------------------------
printf 'sources     %s (%s)\n' "$TARGETS" "$BREAKDOWN"
printf 'rendered    %s — these are the slow ones\n' "$RENDERED"
if [ "$TERMS" -eq 0 ]; then
  printf 'search      NONE SET — every posting on every board goes to the evaluator\n'
else
  printf 'search      %s term(s)\n' "$TERMS"
fi
printf 'log         %s\n' "$LOG"

ALERTS=(--no-alerts)
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --alerts) ALERTS=() ;;
    *) ARGS+=("$arg") ;;
  esac
done

# systemd-inhibit keeps the machine awake for the run. Being on PATH is not
# enough: without a systemd user bus — over SSH, in a container, in some
# session setups — it exits immediately and takes the run down with it, which
# is the exact failure that leaves nothing to read in the morning. So it is
# tried for real first, and skipped if it does not work.
WRAPPER=()
if command -v systemd-inhibit >/dev/null 2>&1 \
   && systemd-inhibit --what=sleep --why="probe" true >/dev/null 2>&1; then
  WRAPPER=(systemd-inhibit --what=sleep:idle --why="ACIDE-Watch inspection")
elif command -v caffeinate >/dev/null 2>&1; then
  WRAPPER=(caffeinate -s)
else
  printf 'note        cannot inhibit sleep here; suspend would pause the run\n'
fi

setsid nohup "${WRAPPER[@]}" "$ACIDE" inspect "${ALERTS[@]}" "${ARGS[@]}" \
  >> "$LOG" 2>&1 < /dev/null &
echo $! > "$PIDFILE"

printf '\nstarted     pid %s\n' "$(cat "$PIDFILE")"
printf 'watch       tail -f %s\n' "$LOG"
printf 'stop        kill %s\n' "$(cat "$PIDFILE")"
printf '\nStopping is safe: each source is saved as it finishes.\n'
