#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ID="core-memory-bridge"
CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-/home/node/.openclaw/openclaw.json}"
INSTALL_PATH="${OPENCLAW_EXTENSIONS_DIR:-/home/node/.openclaw/extensions}/${PLUGIN_ID}"
BEADS_ROOT="${CORE_MEMORY_BEADS_ROOT:-/home/node/.openclaw/workspace/Core-Memory/.beads/events}"

pass() { printf 'PASS %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*"; }

status=0

if openclaw plugins list | grep -qi "$PLUGIN_ID"; then
  pass "plugin is listed"
else
  fail "plugin not listed"
  status=1
fi

if [ -f "$CONFIG_PATH" ]; then
  if python3 - <<PY
import json
from pathlib import Path
cfg = json.loads(Path(${CONFIG_PATH@Q}).read_text())
entries = ((cfg.get('plugins') or {}).get('entries') or {})
raise SystemExit(1 if 'core-memory-bridge' in entries else 0)
PY
  then
    pass "no stale plugins.entries.core-memory-bridge"
  else
    fail "stale plugins.entries.core-memory-bridge is present"
    status=1
  fi
else
  warn "config not found at $CONFIG_PATH"
fi

if openclaw logs --limit 120 --plain | grep -Eqi 'blocked plugin candidate|stale config entry ignored'; then
  fail "recent logs contain blocked/stale plugin warnings"
  status=1
else
  pass "no blocked/stale plugin warnings in recent logs"
fi

if [ -f "/tmp/core-memory-bridge-hook.log" ]; then
  pass "hook log exists"
  tail -n 3 /tmp/core-memory-bridge-hook.log || true
else
  warn "hook log missing (/tmp/core-memory-bridge-hook.log)"
fi

for f in memory-pass-status.jsonl memory-events.jsonl; do
  p="$BEADS_ROOT/$f"
  if [ -f "$p" ]; then
    pass "$f exists"
    tail -n 2 "$p" || true
  else
    warn "$f missing at $p"
  fi
done

exit $status
