from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _run(cmd: list[str], dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return {"ok": True, "dry_run": True, "cmd": cmd}
    p = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "ok": p.returncode == 0,
        "cmd": cmd,
        "returncode": p.returncode,
        "stdout": (p.stdout or "").strip(),
        "stderr": (p.stderr or "").strip(),
    }


def run_openclaw_onboard(
    *,
    openclaw_bin: str = "openclaw",
    plugin_dir: str | None = None,
    replace_memory_core: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    default_plugin_dir = repo_root / "plugins" / "openclaw-core-memory-bridge"
    plugin_path = Path(plugin_dir) if plugin_dir else default_plugin_dir

    steps: list[dict[str, Any]] = []

    steps.append(_run([openclaw_bin, "plugins", "install", str(plugin_path)], dry_run=dry_run))
    steps.append(_run([openclaw_bin, "plugins", "enable", "core-memory-bridge"], dry_run=dry_run))

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
        "plugin_path": str(plugin_path),
        "steps": steps,
    }


def render_onboard_report(payload: dict[str, Any]) -> str:
    lines = [
        f"openclaw_onboard: {'ok' if payload.get('ok') else 'failed'}",
        f"mode: {payload.get('mode')}",
        f"plugin_path: {payload.get('plugin_path')}",
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
    return "\n".join(lines)


if __name__ == "__main__":
    result = run_openclaw_onboard()
    print(json.dumps(result, indent=2))
