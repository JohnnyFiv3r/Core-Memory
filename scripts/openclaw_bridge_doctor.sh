#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ID="core-memory-bridge"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT_DEFAULT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="${CORE_MEMORY_REPO:-$REPO_ROOT_DEFAULT}"
OPENCLAW_HOME_DEFAULT="${HOME:-/tmp}/.openclaw"
CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$OPENCLAW_HOME_DEFAULT/openclaw.json}"
INSTALL_PATH="${OPENCLAW_EXTENSIONS_DIR:-$OPENCLAW_HOME_DEFAULT/extensions}/${PLUGIN_ID}"
CORE_MEMORY_ROOT_ACTUAL="${CORE_MEMORY_ROOT:-$REPO_ROOT}"
BEADS_ROOT="${CORE_MEMORY_BEADS_ROOT:-$CORE_MEMORY_ROOT_ACTUAL/.beads/events}"
HOOK_LOG="${CORE_MEMORY_BRIDGE_HOOK_LOG:-/tmp/core-memory-bridge-hook.log}"
HOSTED_CLONE_ENABLED="unknown"
LOCAL_WRITE_ENABLED="true"

pass() { printf 'PASS %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*"; }

status=0

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "missing required command: $1"
    status=1
    return 1
  fi
  return 0
}

need_cmd openclaw || true
need_cmd python3 || true

if [ "$status" -ne 0 ]; then
  printf '\nRemediation: install OpenClaw/python3 in this runtime, rerun core-memory openclaw onboard, restart OpenClaw, then rerun this doctor.\n'
  exit "$status"
fi

if plugins_output="$(openclaw plugins list 2>&1)"; then
  if grep -qi "$PLUGIN_ID" <<<"$plugins_output"; then
    pass "plugin is listed"
  else
    fail "plugin not listed"
    status=1
  fi
else
  fail "openclaw plugins list failed: $plugins_output"
  status=1
fi

if [ -d "$INSTALL_PATH" ]; then
  pass "plugin install path exists at $INSTALL_PATH"
  if [ -f "$INSTALL_PATH/index.js" ] \
    && grep -q "core_memory.integrations.openclaw.agent_end_bridge" "$INSTALL_PATH/index.js" \
    && grep -q "core_memory.integrations.openclaw.hosted_capture_bridge" "$INSTALL_PATH/index.js" \
    && grep -q "core_memory.integrations.openclaw.read_bridge" "$INSTALL_PATH/index.js" \
    && grep -q "core_memory.integrations.openclaw.compaction_queue" "$INSTALL_PATH/index.js" \
    && ! grep -q "core_memory.integrations.openclaw_agent_end_bridge" "$INSTALL_PATH/index.js" \
    && ! grep -q "core_memory.integrations.openclaw_read_bridge" "$INSTALL_PATH/index.js" \
    && ! grep -q "core_memory.integrations.openclaw_compaction_queue" "$INSTALL_PATH/index.js"; then
    pass "installed plugin uses canonical bridge Python module paths"
  else
    fail "installed plugin has stale bridge Python module paths; rerun core-memory openclaw onboard or scripts/openclaw_bridge_install.sh"
    status=1
  fi
else
  fail "plugin install path missing at $INSTALL_PATH"
  status=1
fi

if PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}" python3 - <<'PY'
import importlib

for module in (
    "core_memory.integrations.openclaw.agent_end_bridge",
    "core_memory.integrations.openclaw.hosted_capture_bridge",
    "core_memory.integrations.openclaw.read_bridge",
    "core_memory.integrations.openclaw.compaction_queue",
):
    importlib.import_module(module)
PY
then
  pass "canonical bridge Python modules import from CORE_MEMORY_REPO=$REPO_ROOT"
else
  fail "canonical bridge Python modules failed to import from CORE_MEMORY_REPO=$REPO_ROOT"
  status=1
fi

if [ -f "$CONFIG_PATH" ]; then
  set +e
  config_summary="$(python3 - <<PY
import json
import os
from pathlib import Path

cfg = json.loads(Path(${CONFIG_PATH@Q}).read_text())
plugins = cfg.get("plugins") or {}
entries = plugins.get("entries") or {}
allow = plugins.get("allow") or []
if not isinstance(allow, list):
    allow = [allow]
entry = entries.get("core-memory-bridge") if isinstance(entries, dict) else None
if not isinstance(entry, dict):
    raise SystemExit(1)
hooks = entry.get("hooks") if isinstance(entry.get("hooks"), dict) else {}
if hooks.get("allowConversationAccess") is not True:
    raise SystemExit(2)
config = entry.get("config") if isinstance(entry.get("config"), dict) else {}
if not config.get("coreMemoryRepo"):
    raise SystemExit(3)
if "core-memory-bridge" not in allow:
    raise SystemExit(4)
hosted_url = (
    os.environ.get("SATORID_OPENCLAW_CORE_MEMORY_URL")
    or os.environ.get("CORE_MEMORY_HOSTED_TURN_FINALIZED_URL")
    or os.environ.get("CORE_MEMORY_HOSTED_API_BASE_URL")
    or config.get("hostedCoreMemoryUrl")
    or ""
)
hosted_token = (
    os.environ.get("SATORID_GATEWAY_KEY")
    or os.environ.get("SATORID_CORE_MEMORY_HTTP_TOKEN")
    or os.environ.get("CORE_MEMORY_HOSTED_HTTP_TOKEN")
    or config.get("hostedCoreMemoryToken")
    or ""
)
hosted_enabled = bool(config.get("enableHostedCoreMemoryClone")) or bool(hosted_url)
local_write_enabled = config.get("enableLocalCoreMemoryWrite", True) is not False
if hosted_enabled and not hosted_url:
    raise SystemExit(5)
if hosted_enabled and not hosted_token:
    raise SystemExit(6)
print(f"HOSTED_CLONE_ENABLED={'true' if hosted_enabled else 'false'}")
print(f"LOCAL_WRITE_ENABLED={'true' if local_write_enabled else 'false'}")
PY
)"
  config_status=$?
  set -e
  if [ "$config_status" -eq 0 ]; then
    while IFS='=' read -r key value; do
      case "$key" in
        HOSTED_CLONE_ENABLED) HOSTED_CLONE_ENABLED="$value" ;;
        LOCAL_WRITE_ENABLED) LOCAL_WRITE_ENABLED="$value" ;;
      esac
    done <<<"$config_summary"
    pass "config has plugins.entries.$PLUGIN_ID with hooks.allowConversationAccess, coreMemoryRepo, and plugins.allow"
    if [ "$HOSTED_CLONE_ENABLED" = "true" ]; then
      pass "hosted Core Memory clone is configured"
    else
      warn "hosted Core Memory clone is not configured; set CORE_MEMORY_HOSTED_TURN_FINALIZED_URL and CORE_MEMORY_HOSTED_HTTP_TOKEN for hosted capture"
    fi
    if [ "$LOCAL_WRITE_ENABLED" = "true" ]; then
      pass "local Core Memory writes are enabled"
    else
      pass "local Core Memory writes are disabled; doctor will not require local .beads/events movement"
    fi
  else
    fail "config hardening incomplete: set plugins.entries.$PLUGIN_ID.hooks.allowConversationAccess=true, config.coreMemoryRepo, plugins.allow, and hosted clone URL/token when enableHostedCoreMemoryClone=true"
    status=1
  fi
else
  fail "config not found at $CONFIG_PATH"
  status=1
fi

logs="$(openclaw logs --limit 200 --plain 2>&1 || true)"
if grep -Eqi "($PLUGIN_ID.*(blocked plugin candidate|stale config entry ignored))|((blocked plugin candidate|stale config entry ignored).*$PLUGIN_ID)" <<<"$logs"; then
  fail "recent logs contain $PLUGIN_ID blocked/stale plugin warnings"
  status=1
else
  pass "no $PLUGIN_ID blocked/stale plugin warnings in recent logs"
fi
if grep -qi "$PLUGIN_ID" <<<"$logs"; then
  pass "recent OpenClaw logs mention $PLUGIN_ID"
else
  fail "recent OpenClaw logs do not mention $PLUGIN_ID; plugin may be enabled on disk but not loaded by the runtime"
  status=1
fi

if [ -f "$HOOK_LOG" ]; then
  if grep -q "register coreMemoryRoot=" "$HOOK_LOG"; then
    pass "hook log has bridge register line"
  else
    fail "hook log exists but has no bridge register line"
    status=1
  fi
  if grep -q "module_check module=core_memory.integrations.openclaw.agent_end_bridge ok=true" "$HOOK_LOG"; then
    pass "hook log has agent_end module_check ok"
  else
    fail "hook log missing successful agent_end module_check"
    status=1
  fi
  if grep -q "module_check module=core_memory.integrations.openclaw.hosted_capture_bridge ok=true" "$HOOK_LOG"; then
    pass "hook log has hosted_capture module_check ok"
  else
    fail "hook log missing successful hosted_capture module_check"
    status=1
  fi
  if [ "$HOSTED_CLONE_ENABLED" = "true" ]; then
    if grep -q "hosted_clone ok=true emitted=true" "$HOOK_LOG"; then
      pass "hook log has hosted_clone emitted movement"
    elif grep -q "hosted_clone" "$HOOK_LOG"; then
      warn "hook log mentions hosted_clone but has no emitted hosted receipt yet"
    else
      warn "hook log has no hosted_clone movement yet; send a user turn after restart to verify hosted capture"
    fi
  fi
  if grep -q "agent_end session=" "$HOOK_LOG"; then
    pass "hook log has agent_end lifecycle movement"
  elif grep -q "fallback_result ok=true emitted=true" "$HOOK_LOG"; then
    pass "hook log has streaming message fallback bead movement"
  elif grep -q "message_received captured" "$HOOK_LOG" && grep -q "message_sent observed" "$HOOK_LOG"; then
    warn "hook log has message fallback observations but no emitted fallback result yet"
  else
    warn "hook log has no agent_end or streaming fallback movement yet; send a user turn after restart to verify live write movement"
  fi
  tail -n 5 "$HOOK_LOG" || true
else
  fail "hook log missing ($HOOK_LOG); runtime did not register $PLUGIN_ID or log path is inaccessible"
  status=1
fi

for f in memory-pass-status.jsonl memory-events.jsonl; do
  p="$BEADS_ROOT/$f"
  if [ -f "$p" ]; then
    pass "$f exists at $p"
    tail -n 2 "$p" || true
  elif [ "$LOCAL_WRITE_ENABLED" = "false" ]; then
    warn "$f missing at $p because local Core Memory writes are disabled"
  else
    fail "$f missing at $p"
    status=1
  fi
done

if [ "$status" -ne 0 ]; then
  printf '\nRemediation for Codex/OpenClaw runtime migrations:\n'
  printf '1. From the Core Memory repo, run: core-memory openclaw onboard\n'
  printf '2. Restart the OpenClaw gateway/container that owns Telegram/Codex traffic.\n'
  printf '3. Rerun: %s\n' "$0"
  printf '4. Send a Telegram turn and confirm %s gets hosted_clone movement; if local writes are enabled, also confirm .beads/events file movement under %s.\n' "$HOOK_LOG" "$BEADS_ROOT"
fi

exit "$status"
