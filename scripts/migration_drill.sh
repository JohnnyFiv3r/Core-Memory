#!/usr/bin/env bash
set -euo pipefail

# End-to-end migration drill:
# 1) seed minimal legacy store
# 2) run core migrate-store
# 3) verify counts + idempotency

ROOT="${1:-/tmp/core-memory-drill}"
LEGACY="$ROOT/legacy"
CORE="$ROOT/core"
CORE_MEMORY_BIN="${CORE_MEMORY_BIN:-$(pwd)/.venv/bin/core-memory}"

rm -rf "$ROOT"
mkdir -p "$LEGACY" "$CORE"

cat > "$LEGACY/session-s1.jsonl" <<'JSONL'
{"id":"bead-LEGACYA","type":"decision","created_at":"2026-03-02T00:00:00+00:00","session_id":"s1","title":"Legacy A","summary":["a"],"detail":"","scope":"project","authority":"agent_inferred","confidence":0.8,"tags":["legacy"],"status":"open"}
{"id":"bead-LEGACYB","type":"decision","created_at":"2026-03-02T00:01:00+00:00","session_id":"s1","title":"Legacy B","summary":["b"],"detail":"","scope":"project","authority":"agent_inferred","confidence":0.8,"tags":["legacy"],"status":"open"}
JSONL

cat > "$LEGACY/edges.jsonl" <<'JSONL'
{"id":"edge-LEGACY1","source_id":"bead-LEGACYB","target_id":"bead-LEGACYA","type":"follows","created_at":"2026-03-02T00:02:00+00:00"}
JSONL

cat > "$LEGACY/index.json" <<'JSON'
{"beads":{"bead-LEGACYA":{"type":"decision","session_id":"s1","status":"open","title":"Legacy A","file":"session-s1.jsonl","line":0,"created_at":"2026-03-02T00:00:00+00:00","tags":["legacy"],"scope":"project"},"bead-LEGACYB":{"type":"decision","session_id":"s1","status":"open","title":"Legacy B","file":"session-s1.jsonl","line":1,"created_at":"2026-03-02T00:01:00+00:00","tags":["legacy"],"scope":"project"}},"stats":{"total_beads":2}}
JSON

echo "[1/3] First migrate-store run"
"$CORE_MEMORY_BIN" --root "$CORE" migrate-store --legacy-root "$LEGACY"

echo "[2/3] Second migrate-store run (idempotency)"
"$CORE_MEMORY_BIN" --root "$CORE" migrate-store --legacy-root "$LEGACY"

echo "[3/3] Validate core stats"
"$CORE_MEMORY_BIN" --root "$CORE" stats

echo "Migration drill complete: $ROOT"
