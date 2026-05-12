"""CLI helpers for `core-memory mcp ...`."""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Any

from core_memory.integrations.mcp.constants import MCP_HTTP_PATH, MCP_SPEC_VERSION

DEFAULT_MCP_PORT = 8000
DEFAULT_MCP_ROOT = "~/.core-memory/store"
SERVER_NAME = "core-memory"
SUPPORTED_CLIENTS = {"claude-code", "cursor", "windsurf", "open-webui"}


def sdk_version() -> str:
    try:
        return metadata.version("mcp")
    except metadata.PackageNotFoundError:
        return "not-installed"


def version_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "mcp_spec_version": MCP_SPEC_VERSION,
        "mcp_sdk_package": "mcp",
        "mcp_sdk_version": sdk_version(),
    }


def mcp_url(port: int = DEFAULT_MCP_PORT) -> str:
    return f"http://localhost:{int(port)}{MCP_HTTP_PATH}"


def _health_url(port: int = DEFAULT_MCP_PORT) -> str:
    return f"{mcp_url(port)}/healthz"


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def status_payload(*, port: int = DEFAULT_MCP_PORT, timeout: float = 1.0) -> dict[str, Any]:
    url = _health_url(port)
    out: dict[str, Any] = {"ok": False, "url": mcp_url(port), "health_url": url, "port_open": _port_open(port)}
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310 - local operator URL
            body = resp.read().decode("utf-8")
            out.update({"ok": 200 <= resp.status < 300, "status_code": resp.status, "health": json.loads(body or "{}")})
    except urllib.error.HTTPError as exc:
        out.update({"status_code": exc.code, "error": str(exc)})
    except Exception as exc:
        out.update({"error": str(exc)})
    return out


def _home() -> Path:
    return Path.home()


def client_config_candidates(client: str) -> list[Path]:
    home = _home()
    if client == "claude-code":
        return [home / ".claude.json", home / ".claude" / "mcp.json"]
    if client == "cursor":
        return [home / ".cursor" / "mcp.json"]
    if client == "windsurf":
        return [
            home / ".codeium" / "windsurf" / "mcp_config.json",
            home / ".config" / "Windsurf" / "User" / "mcp_config.json",
        ]
    if client == "open-webui":
        return [home / ".open-webui" / "mcp_config.json"]
    return []


def detect_clients() -> list[str]:
    found: list[str] = []
    for client in sorted(SUPPORTED_CLIENTS):
        if any(path.exists() for path in client_config_candidates(client)):
            found.append(client)
    return found


def _pick_config_path(client: str) -> Path | None:
    candidates = client_config_candidates(client)
    for path in candidates:
        if path.exists():
            return path
    return candidates[0] if candidates else None


def _read_json_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"config is not valid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"config root must be a JSON object: {path}")
    return value


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _server_entry(*, port: int) -> dict[str, Any]:
    return {"url": mcp_url(port)}


def install_client_config(client: str, *, port: int, dry_run: bool = False) -> dict[str, Any]:
    if client not in SUPPORTED_CLIENTS:
        return {
            "ok": False,
            "client": client,
            "error": {"code": "unsupported_client", "message": f"unsupported MCP client: {client}"},
        }
    path = _pick_config_path(client)
    if path is None:
        return {
            "ok": False,
            "client": client,
            "error": {"code": "unsupported_client", "message": f"unsupported MCP client: {client}"},
        }
    existed = path.exists()
    if not existed and client not in detect_clients():
        return {
            "ok": False,
            "client": client,
            "config_path": str(path),
            "error": {
                "code": "client_config_not_found",
                "message": "client config was not found; see README manual MCP JSON fallback",
            },
        }
    data = _read_json_config(path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"mcpServers must be a JSON object: {path}")
    servers[SERVER_NAME] = _server_entry(port=port)
    if not dry_run:
        _atomic_write_json(path, data)
    return {"ok": True, "client": client, "config_path": str(path), "server": servers[SERVER_NAME], "dry_run": dry_run}


def _systemd_unit_text(*, root: str, port: int) -> str:
    exe = shutil.which("python3") or shutil.which("python") or sys.executable
    return f"""[Unit]
Description=Core Memory MCP server
After=network.target

[Service]
Type=simple
Environment=CORE_MEMORY_ROOT={root}
Environment=CORE_MEMORY_HTTP_PORT={int(port)}
ExecStart={exe} -m core_memory.integrations.http.server
Restart=on-failure

[Install]
WantedBy=default.target
"""


def _launchd_plist_text(*, root: str, port: int) -> str:
    exe = shutil.which("python3") or shutil.which("python") or sys.executable
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>dev.linelead.core-memory-mcp</string>
  <key>ProgramArguments</key>
  <array><string>{exe}</string><string>-m</string><string>core_memory.integrations.http.server</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CORE_MEMORY_ROOT</key><string>{root}</string>
    <key>CORE_MEMORY_HTTP_PORT</key><string>{int(port)}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
"""


def install_service(*, root: str, port: int, no_start: bool, dry_run: bool = False) -> dict[str, Any]:
    system = platform.system().lower()
    expanded_root = str(Path(root).expanduser())
    Path(expanded_root).mkdir(parents=True, exist_ok=True)
    if system == "linux":
        path = _home() / ".config" / "systemd" / "user" / "core-memory-mcp.service"
        text = _systemd_unit_text(root=expanded_root, port=port)
        commands = [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "core-memory-mcp.service"],
        ]
        if not no_start:
            commands.append(["systemctl", "--user", "restart", "core-memory-mcp.service"])
    elif system == "darwin":
        path = _home() / "Library" / "LaunchAgents" / "dev.linelead.core-memory-mcp.plist"
        text = _launchd_plist_text(root=expanded_root, port=port)
        commands = [] if no_start else [["launchctl", "load", "-w", str(path)]]
    else:
        return {
            "ok": True,
            "service": "manual",
            "message": "Windows service install is not automated in v1; run the HTTP server manually.",
            "dry_run": dry_run,
        }
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        for cmd in commands:
            subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "service_path": str(path), "commands": commands, "no_start": no_start, "dry_run": dry_run}


def install_payload(
    *,
    client: str | None = None,
    root: str | None = None,
    port: int = DEFAULT_MCP_PORT,
    no_start: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    chosen_root = str(Path(root or os.getenv("CORE_MEMORY_ROOT") or DEFAULT_MCP_ROOT).expanduser())
    clients = [client] if client else detect_clients()
    if not clients:
        return {
            "ok": False,
            "error": {
                "code": "no_clients_detected",
                "message": "No supported MCP client config was detected; see README manual MCP JSON fallback.",
            },
            "manual": {"mcpServers": {SERVER_NAME: _server_entry(port=port)}},
            "root": chosen_root,
            "url": mcp_url(port),
        }
    results = [install_client_config(c, port=port, dry_run=dry_run) for c in clients]
    service = install_service(root=chosen_root, port=port, no_start=no_start, dry_run=dry_run)
    ok = all(r.get("ok") for r in results) and bool(service.get("ok"))
    verify = (
        status_payload(port=port, timeout=1.0)
        if ok and not no_start and not dry_run
        else {"ok": False, "skipped": True}
    )
    return {
        "ok": ok,
        "clients": results,
        "service": service,
        "verify": verify,
        "root": chosen_root,
        "url": mcp_url(port),
    }


def uninstall_payload(*, client: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    clients = [client] if client else detect_clients()
    results: list[dict[str, Any]] = []
    for c in clients:
        path = _pick_config_path(c)
        if path is None or not path.exists():
            results.append({"ok": False, "client": c, "error": {"code": "client_config_not_found"}})
            continue
        data = _read_json_config(path)
        servers = data.get("mcpServers")
        removed = False
        if isinstance(servers, dict) and SERVER_NAME in servers:
            removed = True
            servers.pop(SERVER_NAME, None)
            if not dry_run:
                _atomic_write_json(path, data)
        results.append({"ok": True, "client": c, "config_path": str(path), "removed": removed, "dry_run": dry_run})
    return {"ok": all(r.get("ok") for r in results), "clients": results}
