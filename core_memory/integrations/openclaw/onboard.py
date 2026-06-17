from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .flags import supersede_openclaw_summary_enabled, runtime_flags_snapshot

PLUGIN_ID = "core-memory-bridge"


def _run(cmd: list[str], dry_run: bool = False, *, allow_failure: bool = False) -> dict[str, Any]:
    if dry_run:
        return {"ok": True, "dry_run": True, "cmd": cmd}
    p = subprocess.run(cmd, capture_output=True, text=True)
    ok = p.returncode == 0 or allow_failure
    return {
        "ok": ok,
        "cmd": cmd,
        "returncode": p.returncode,
        "allowed_failure": bool(allow_failure and p.returncode != 0),
        "stdout": (p.stdout or "").strip(),
        "stderr": (p.stderr or "").strip(),
    }


def default_openclaw_config_path() -> Path:
    if os.environ.get("OPENCLAW_CONFIG_PATH"):
        return Path(str(os.environ["OPENCLAW_CONFIG_PATH"]))
    return Path.home() / ".openclaw" / "openclaw.json"


def harden_openclaw_plugin_config(
    *,
    config_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    cfg_path = Path(config_path) if config_path else default_openclaw_config_path()
    cmd = ["patch_openclaw_config", str(cfg_path)]
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "cmd": cmd,
            "config_path": str(cfg_path),
            "stdout": f"would remove stale plugins.entries.{PLUGIN_ID} and enforce plugins.allow",
        }
    if not cfg_path.exists():
        return {
            "ok": False,
            "cmd": cmd,
            "config_path": str(cfg_path),
            "stderr": f"missing config: {cfg_path}",
        }

    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "cmd": cmd,
            "config_path": str(cfg_path),
            "stderr": f"invalid config JSON: {exc}",
        }

    plugins = cfg.setdefault("plugins", {})
    entries = plugins.get("entries")
    removed_stale_entry = False
    if isinstance(entries, dict):
        removed_stale_entry = PLUGIN_ID in entries
        entries.pop(PLUGIN_ID, None)

    allow = plugins.get("allow")
    if allow is None:
        allow_items: list[Any] = []
    elif isinstance(allow, list):
        allow_items = list(allow)
    else:
        allow_items = [allow]

    added_allow = PLUGIN_ID not in allow_items
    if added_allow:
        allow_items.append(PLUGIN_ID)
    plugins["allow"] = allow_items

    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "cmd": cmd,
        "config_path": str(cfg_path),
        "removed_stale_entry": removed_stale_entry,
        "added_allow": added_allow,
        "stdout": f"updated {cfg_path}",
    }


def run_openclaw_onboard(
    *,
    openclaw_bin: str = "openclaw",
    plugin_dir: str | None = None,
    config_path: str | Path | None = None,
    replace_memory_core: bool | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[3]
    default_plugin_dir = repo_root / "plugins" / "openclaw-core-memory-bridge"
    plugin_path = Path(plugin_dir) if plugin_dir else default_plugin_dir

    if replace_memory_core is None:
        replace_memory_core = supersede_openclaw_summary_enabled()

    steps: list[dict[str, Any]] = []

    steps.append(_run([openclaw_bin, "plugins", "uninstall", PLUGIN_ID], dry_run=dry_run, allow_failure=True))
    steps.append(_run([openclaw_bin, "plugins", "install", str(plugin_path)], dry_run=dry_run))
    steps.append(harden_openclaw_plugin_config(config_path=config_path, dry_run=dry_run))
    steps.append(_run([openclaw_bin, "plugins", "enable", PLUGIN_ID], dry_run=dry_run))

    if replace_memory_core:
        steps.append(_run([openclaw_bin, "plugins", "disable", "memory-core"], dry_run=dry_run))
    else:
        # explicitly keep memory-core enabled for coexistence mode
        steps.append(_run([openclaw_bin, "plugins", "enable", "memory-core"], dry_run=dry_run))

    if not dry_run:
        steps.append(_run([openclaw_bin, "status", "--deep"], dry_run=False))

    ok = all(bool(s.get("ok")) for s in steps)
    mode = "replace" if replace_memory_core else "coexist"
    return {
        "ok": ok,
        "mode": mode,
        "replace_memory_core": bool(replace_memory_core),
        "flags": runtime_flags_snapshot(),
        "plugin_path": str(plugin_path),
        "config_path": str(Path(config_path) if config_path else default_openclaw_config_path()),
        "restart_required": True,
        "doctor_command": "scripts/openclaw_bridge_doctor.sh",
        "remediation": "restart OpenClaw, rerun the bridge doctor, and verify /tmp/core-memory-bridge-hook.log plus memory event append movement",
        "steps": steps,
    }


def render_onboard_report(payload: dict[str, Any]) -> str:
    lines = [
        f"openclaw_onboard: {'ok' if payload.get('ok') else 'failed'}",
        f"mode: {payload.get('mode')}",
        f"replace_memory_core: {payload.get('replace_memory_core')}",
        f"plugin_path: {payload.get('plugin_path')}",
        f"config_path: {payload.get('config_path')}",
    ]
    for i, step in enumerate(payload.get("steps") or [], start=1):
        cmd = " ".join(step.get("cmd") or [])
        lines.append(f"[{i}] {'ok' if step.get('ok') else 'fail'} :: {cmd}")
        out = (step.get("stdout") or "").strip()
        err = (step.get("stderr") or "").strip()
        if out:
            lines.append(f"  stdout: {out[:500]}")
        if err:
            lines.append(f"  stderr: {err[:500]}")
    if payload.get("restart_required"):
        lines.append("restart_required: true")
        lines.append("next: restart OpenClaw gateway/container, then run scripts/openclaw_bridge_doctor.sh")
        lines.append("verify: /tmp/core-memory-bridge-hook.log has register/module_check lines and .beads/events files move after a turn")
    return "\n".join(lines)


if __name__ == "__main__":
    result = run_openclaw_onboard()
    print(json.dumps(result, indent=2))
