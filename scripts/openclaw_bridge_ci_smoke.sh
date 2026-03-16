#!/usr/bin/env bash
set -euo pipefail

# CI smoke gate for Core Memory OpenClaw bridge.
# Requires:
# - openclaw CLI available
# - Core-Memory repo mounted at /home/node/.openclaw/workspace/Core-Memory (override via env)

REPO_ROOT="${CORE_MEMORY_REPO:-/home/node/.openclaw/workspace/Core-Memory}"
EVENTS_DIR="${CORE_MEMORY_EVENTS_DIR:-$REPO_ROOT/.beads/events}"
EVENTS_FILE="$EVENTS_DIR/memory-events.jsonl"
PASS_FILE="$EVENTS_DIR/memory-pass-status.jsonl"

cd "$REPO_ROOT"

echo "[ci-smoke] baseline counts"
base_events=0
base_pass=0
[ -f "$EVENTS_FILE" ] && base_events=$(wc -l < "$EVENTS_FILE" | tr -d ' ')
[ -f "$PASS_FILE" ] && base_pass=$(wc -l < "$PASS_FILE" | tr -d ' ')
echo "[ci-smoke] events=$base_events pass=$base_pass"

# Synthetic finalized-turn payload (CI-safe, deterministic, no live chat required)
python3 -m core_memory.integrations.openclaw_agent_end_bridge <<'JSON'
{
  "event": {
    "runId": "ci-smoke-run-001",
    "success": true,
    "messages": [
      {"role": "user", "content": "CI smoke synthetic user turn"},
      {"role": "assistant", "content": "CI smoke synthetic assistant final"}
    ]
  },
  "ctx": {
    "sessionId": "ci-smoke",
    "sessionKey": "ci-smoke",
    "trigger": "user"
  },
  "root": "."
}
JSON

new_events=0
new_pass=0
[ -f "$EVENTS_FILE" ] && new_events=$(wc -l < "$EVENTS_FILE" | tr -d ' ')
[ -f "$PASS_FILE" ] && new_pass=$(wc -l < "$PASS_FILE" | tr -d ' ')
echo "[ci-smoke] events=$new_events pass=$new_pass"

if [ "$new_events" -le "$base_events" ]; then
  echo "[ci-smoke] FAIL: memory-events.jsonl did not grow"
  exit 1
fi
if [ "$new_pass" -le "$base_pass" ]; then
  echo "[ci-smoke] FAIL: memory-pass-status.jsonl did not grow"
  exit 1
fi

echo "[ci-smoke] PASS: synthetic finalized-turn append verified"
