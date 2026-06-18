#!/usr/bin/env bash
set -euo pipefail

# Hardened installer for Core Memory OpenClaw bridge.
# Idempotent by design.

PLUGIN_ID="core-memory-bridge"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT_DEFAULT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLUGIN_SRC_DEFAULT="$REPO_ROOT_DEFAULT/plugins/openclaw-core-memory-bridge"
PLUGIN_SRC="${1:-${CORE_MEMORY_BRIDGE_SOURCE:-$PLUGIN_SRC_DEFAULT}}"
OPENCLAW_HOME_DEFAULT="${HOME:-/tmp}/.openclaw"
CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$OPENCLAW_HOME_DEFAULT/openclaw.json}"
INSTALL_PATH="${OPENCLAW_EXTENSIONS_DIR:-$OPENCLAW_HOME_DEFAULT/extensions}/${PLUGIN_ID}"
HOOK_LOG="${CORE_MEMORY_BRIDGE_HOOK_LOG:-/tmp/core-memory-bridge-hook.log}"

log() { printf '[core-memory-bridge/install] %s\n' "$*"; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  }
}

need_cmd openclaw
need_cmd python3

run_with_timeout() {
  local seconds="$1"
  shift
  python3 - "$seconds" "$@" <<'PY'
import subprocess
import sys

raw_timeout = sys.argv[1]
try:
    timeout = float(raw_timeout[:-1] if raw_timeout.endswith("s") else raw_timeout)
except ValueError:
    timeout = 15.0
cmd = sys.argv[2:]
try:
    completed = subprocess.run(cmd, timeout=timeout)
except subprocess.TimeoutExpired:
    print(f"command timed out after {timeout:g}s: {' '.join(cmd)}", file=sys.stderr)
    raise SystemExit(124)
raise SystemExit(completed.returncode)
PY
}

if [ ! -f "$PLUGIN_SRC/openclaw.plugin.json" ]; then
  printf 'Plugin source not found or invalid: %s\n' "$PLUGIN_SRC" >&2
  exit 1
fi

log "uninstall old plugin state with timeout (safe if absent)"
run_with_timeout "${OPENCLAW_PLUGIN_UNINSTALL_TIMEOUT:-15s}" openclaw plugins uninstall "$PLUGIN_ID" >/dev/null 2>&1 || true

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

if touch "$HOOK_LOG" 2>/dev/null; then
  chmod a+rw "$HOOK_LOG" 2>/dev/null || true
  log "normalized hook log permissions at $HOOK_LOG"
else
  log "WARN: could not normalize hook log permissions at $HOOK_LOG"
fi

log "patch config: set plugins.entries.$PLUGIN_ID with conversation access and enforce plugins.allow"
python3 - <<PY
import json
import os
from pathlib import Path

plugin_id = ${PLUGIN_ID@Q}
cfg_path = Path(${CONFIG_PATH@Q})
repo_root = ${REPO_ROOT_DEFAULT@Q}
if not cfg_path.exists():
    raise SystemExit(f"missing config: {cfg_path}")

cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
plugins = cfg.setdefault('plugins', {})

entries = plugins.get('entries')
if not isinstance(entries, dict):
    entries = {}

existing = entries.get(plugin_id) if isinstance(entries.get(plugin_id), dict) else {}
existing_config = existing.get('config') if isinstance(existing.get('config'), dict) else {}
entry_config = {
    'pythonBin': os.environ.get('CORE_MEMORY_PYTHON') or existing_config.get('pythonBin') or 'python3',
    'coreMemoryRoot': os.environ.get('CORE_MEMORY_ROOT') or existing_config.get('coreMemoryRoot') or repo_root,
    'coreMemoryRepo': os.environ.get('CORE_MEMORY_REPO') or existing_config.get('coreMemoryRepo') or repo_root,
    'enableAgentEnd': existing_config.get('enableAgentEnd', True),
    'enableMemorySearch': existing_config.get('enableMemorySearch', True),
    'enableCompactionFlush': existing_config.get('enableCompactionFlush', False),
    'enableMessageTurnFallback': existing_config.get('enableMessageTurnFallback', True),
}
if os.environ.get('CORE_MEMORY_MESSAGE_TURN_FALLBACK_DELAY_MS'):
    try:
        entry_config['messageTurnFallbackDelayMs'] = float(os.environ['CORE_MEMORY_MESSAGE_TURN_FALLBACK_DELAY_MS'])
    except ValueError:
        pass
elif 'messageTurnFallbackDelayMs' in existing_config:
    entry_config['messageTurnFallbackDelayMs'] = existing_config['messageTurnFallbackDelayMs']

entry = {
    **existing,
    'enabled': existing.get('enabled', True),
    'hooks': {
        **(existing.get('hooks') if isinstance(existing.get('hooks'), dict) else {}),
        'allowConversationAccess': True,
    },
    'config': entry_config,
}
entries[plugin_id] = entry
plugins['entries'] = entries

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
log "then run: $REPO_ROOT_DEFAULT/scripts/openclaw_bridge_doctor.sh"
log "do not rely on plugins list alone; verify hook register/module_check lines, agent_end or message fallback movement, and .beads/events movement"
