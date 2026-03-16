#!/usr/bin/env bash
set -euo pipefail

# Hardened installer for Core Memory OpenClaw bridge.
# Idempotent by design.

PLUGIN_ID="core-memory-bridge"
PLUGIN_SRC_DEFAULT="/home/node/.openclaw/workspace/Core-Memory/plugins/openclaw-core-memory-bridge"
PLUGIN_SRC="${1:-${CORE_MEMORY_BRIDGE_SOURCE:-$PLUGIN_SRC_DEFAULT}}"
CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-/home/node/.openclaw/openclaw.json}"
INSTALL_PATH="${OPENCLAW_EXTENSIONS_DIR:-/home/node/.openclaw/extensions}/${PLUGIN_ID}"

log() { printf '[core-memory-bridge/install] %s\n' "$*"; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  }
}

need_cmd openclaw
need_cmd python3

if [ ! -f "$PLUGIN_SRC/openclaw.plugin.json" ]; then
  printf 'Plugin source not found or invalid: %s\n' "$PLUGIN_SRC" >&2
  exit 1
fi

log "uninstall old plugin state (safe if absent)"
openclaw plugins uninstall "$PLUGIN_ID" >/dev/null 2>&1 || true

log "install plugin from: $PLUGIN_SRC"
openclaw plugins install "$PLUGIN_SRC"

# Ownership normalization (required by OpenClaw suspicious ownership gate).
if [ -d "$INSTALL_PATH" ]; then
  if chown -R root:root "$INSTALL_PATH" 2>/dev/null; then
    chmod -R a+rX "$INSTALL_PATH"
    log "normalized ownership/perms at $INSTALL_PATH"
  else
    log "WARN: could not chown $INSTALL_PATH (run with root/sudo in containerized setups)"
  fi
fi

log "patch config: remove stale plugins.entries.$PLUGIN_ID and enforce plugins.allow"
python3 - <<PY
import json
from pathlib import Path

plugin_id = ${PLUGIN_ID@Q}
cfg_path = Path(${CONFIG_PATH@Q})
if not cfg_path.exists():
    raise SystemExit(f"missing config: {cfg_path}")

cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
plugins = cfg.setdefault('plugins', {})

entries = plugins.get('entries')
if isinstance(entries, dict):
    entries.pop(plugin_id, None)

allow = plugins.get('allow')
if allow is None:
    allow = []
if not isinstance(allow, list):
    allow = [allow]
if plugin_id not in allow:
    allow.append(plugin_id)
plugins['allow'] = allow

cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding='utf-8')
print(f"updated {cfg_path}")
PY

log "enable plugin"
openclaw plugins enable "$PLUGIN_ID"

log "done. restart gateway/container to apply runtime load"
log "verify with: openclaw plugins list | grep -i $PLUGIN_ID"}