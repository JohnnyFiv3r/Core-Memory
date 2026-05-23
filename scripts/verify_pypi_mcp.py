#!/usr/bin/env python3
from __future__ import annotations

"""Build/install Core Memory like PyPI and verify MCP over HTTP.

This is intentionally black-box: build a wheel, install it into a clean venv,
run `core-memory mcp version`, start `core-memory mcp serve`, then use the MCP
client to initialize and list tools.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import venv


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n", flush=True)
    proc.check_returncode()
    return proc


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def venv_bin(root: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return root / ("Scripts" if os.name == "nt" else "bin") / f"{name}{suffix}"


def _git_visible_files(repo: Path) -> list[Path]:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo)
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=repo)
    names = [x.decode("utf-8") for x in (tracked + untracked).split(b"\0") if x]
    return [Path(name) for name in names]


def _copy_git_visible_source(repo: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for rel in _git_visible_files(repo):
        src = repo / rel
        if not src.is_file():
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(src.read_bytes())


def build_wheel(repo: Path, dist: Path) -> Path:
    build_env = dist.parent / "build-venv"
    src = dist.parent / "src"
    _copy_git_visible_source(repo, src)
    venv.EnvBuilder(with_pip=True, clear=True).create(build_env)
    build_py = venv_python(build_env)
    run([str(build_py), "-m", "pip", "install", "build"], timeout=180)
    run([str(build_py), "-m", "build", "--wheel", "--outdir", str(dist)], cwd=src, timeout=180)
    wheels = sorted(dist.glob("core_memory-*.whl"))
    if not wheels:
        raise RuntimeError("wheel_not_built")
    return wheels[-1]


async def mcp_list_tools(url: str) -> list[str]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [tool.name for tool in tools.tools]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    with tempfile.TemporaryDirectory(prefix="core-memory-pypi-mcp-") as tmp_s:
        tmp = Path(tmp_s)
        dist = tmp / "dist"
        dist.mkdir()
        wheel = build_wheel(repo, dist)
        env_dir = tmp / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
        py = venv_python(env_dir)
        run([str(py), "-m", "pip", "install", f"{wheel}[mcp]"], timeout=240)
        cli = venv_bin(env_dir, "core-memory")
        version = run([str(cli), "mcp", "version"], timeout=30).stdout.strip()
        root = tmp / "store"
        root.mkdir()
        port = free_port()
        proc = subprocess.Popen(
            [str(cli), "mcp", "serve", "--host", "127.0.0.1", "--port", str(port), "--root", str(root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.time() + 20
            url = f"http://127.0.0.1:{port}/mcp/"
            tools: list[str] | None = None
            last_error = ""
            while time.time() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError(f"mcp_serve_exited:{proc.returncode}:{(proc.stdout.read() if proc.stdout else '')}")
                try:
                    tools = asyncio.run(mcp_list_tools(url))
                    break
                except Exception as exc:
                    last_error = str(exc)
                    time.sleep(0.25)
            if tools is None:
                raise RuntimeError(f"mcp_client_initialize_failed:{last_error}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        required = {"capture", "recall", "ingest", "status"}
        missing = sorted(required.difference(tools or []))
        if missing:
            raise RuntimeError(f"mcp_tools_missing:{missing}")
        print(json.dumps({"ok": True, "wheel": str(wheel), "version": version, "tools": tools}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
