"""CLI handlers for `core-memory setup init` (wizard) and `core-memory setup doctor`."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .config.settings import (
    _PROJECT_CONFIG_NAME,
    _USER_CONFIG_PATH,
    find_project_config,
    load_settings,
    write_config,
)

# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

_PRESETS: dict[str, dict[str, Any]] = {
    "local": {
        "backend": "json",
        "vector_backend": "local-faiss",
        "graph_backend": "kuzu",
        "integration": "none",
        "memory": {
            "rolling_window_tokens": 4000,
            "max_beads": 40,
            "dreamer": True,
            "transcript_grounding": True,
        },
    },
    "sqlite": {
        "backend": "sqlite",
        "vector_backend": "local-faiss",
        "graph_backend": "kuzu",
        "integration": "none",
        "memory": {
            "rolling_window_tokens": 4000,
            "max_beads": 40,
            "dreamer": True,
            "transcript_grounding": True,
        },
    },
    "postgres": {
        "backend": "sqlite",
        "vector_backend": "pgvector",
        "graph_backend": "kuzu",
        "integration": "none",
        "postgres": {"dsn": ""},
        "memory": {
            "rolling_window_tokens": 4000,
            "max_beads": 40,
            "dreamer": True,
            "transcript_grounding": True,
        },
    },
    "neo4j": {
        "backend": "json",
        "vector_backend": "local-faiss",
        "graph_backend": "neo4j",
        "integration": "mcp",
        "neo4j": {"uri": "bolt://localhost:7687", "username": "neo4j", "password": ""},
        "memory": {
            "rolling_window_tokens": 4000,
            "max_beads": 40,
            "dreamer": True,
            "transcript_grounding": True,
        },
    },
}

_INSTALL_CHOICES = [
    ("local", "Local/dev — JSONL files, no extra dependencies [default]"),
    ("sqlite", "SQLite — single-file DB, better query indexing"),
    ("postgres", "Postgres — pgvector-backed, recommended for production"),
    ("neo4j", "Neo4j — graph-native traversal (recommended for causal inspection)"),
    ("custom", "Custom — configure manually"),
]

_INTEGRATION_CHOICES = [
    ("mcp", "MCP server (Claude Code, Cursor, etc.)"),
    ("openclaw", "OpenClaw"),
    ("pydanticai", "PydanticAI"),
    ("http", "HTTP/webhook"),
    ("none", "None / configure later"),
]


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

def _prompt(prompt_text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt_text}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return value if value else default


def _prompt_choice(label: str, choices: list[tuple[str, str]], default_idx: int = 0) -> str:
    print(f"\n{label}")
    for i, (key, desc) in enumerate(choices, 1):
        print(f"  {i}. {desc}")
    default_key = choices[default_idx][0]
    try:
        raw = input(f"\n> [{default_key}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default_key
    if not raw:
        return default_key
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(choices):
            return choices[idx][0]
    for key, _ in choices:
        if raw.lower() == key.lower():
            return key
    return default_key


def _interactive_wizard(root: str) -> dict[str, Any]:
    print("\nCore Memory setup")
    print("-" * 30)

    install_type = _prompt_choice("Install type:", _INSTALL_CHOICES)

    if install_type == "custom":
        cfg: dict[str, Any] = {
            "backend": "json",
            "vector_backend": "auto",
            "graph_backend": "kuzu",
        }
    else:
        import copy
        cfg = copy.deepcopy(_PRESETS.get(install_type, _PRESETS["local"]))

    if install_type == "neo4j":
        print("\nNeo4j connection")
        cfg.setdefault("neo4j", {})
        cfg["neo4j"]["uri"] = _prompt("  URI", cfg["neo4j"].get("uri", "bolt://localhost:7687"))
        cfg["neo4j"]["username"] = _prompt("  Username", cfg["neo4j"].get("username", "neo4j"))
        password = _prompt("  Password (leave blank to use CORE_MEMORY_NEO4J_PASSWORD env var)", "")
        if password:
            cfg["neo4j"]["password"] = password

    if install_type == "postgres":
        print("\nPostgres connection")
        cfg.setdefault("postgres", {})
        dsn = _prompt("  DSN (leave blank to use CORE_MEMORY_POSTGRES_DSN env var)", "")
        if dsn:
            cfg["postgres"]["dsn"] = dsn

    integration = _prompt_choice("Runtime integration:", _INTEGRATION_CHOICES, default_idx=0)
    cfg["integration"] = integration

    print("\nMemory behavior (press Enter to accept defaults):")
    mem = cfg.setdefault("memory", {})
    rw = _prompt("  Rolling window size (tokens)", str(mem.get("rolling_window_tokens", 4000)))
    try:
        mem["rolling_window_tokens"] = int(rw)
    except ValueError:
        mem["rolling_window_tokens"] = 4000

    dreamer_raw = _prompt("  Dreamer background process (on/off)", "on" if mem.get("dreamer", True) else "off")
    mem["dreamer"] = dreamer_raw.lower() not in {"off", "false", "0", "no"}

    grounding_raw = _prompt("  Transcript grounding (on/off)", "on" if mem.get("transcript_grounding", True) else "off")
    mem["transcript_grounding"] = grounding_raw.lower() not in {"off", "false", "0", "no"}

    return cfg


def init_command(args: Any) -> None:
    """Handle `core-memory setup init`."""
    use_global = bool(getattr(args, "global_config", False))
    preset = getattr(args, "preset", None)
    force = bool(getattr(args, "force", False))
    root = getattr(args, "root", ".")

    if use_global:
        config_path = _USER_CONFIG_PATH
    else:
        config_path = Path(root) / _PROJECT_CONFIG_NAME

    if config_path.exists() and not force:
        print(
            json.dumps(
                {
                    "ok": False,
                    "skipped": True,
                    "reason": "config already exists — use --force to overwrite",
                    "path": str(config_path),
                },
                indent=2,
            )
        )
        return

    if preset:
        if preset not in _PRESETS:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"unknown preset {preset!r}; valid: {', '.join(_PRESETS)}",
                    },
                    indent=2,
                )
            )
            sys.exit(1)
        import copy
        cfg = copy.deepcopy(_PRESETS[preset])
    else:
        if not sys.stdin.isatty():
            # Non-interactive without preset → use local default
            import copy
            cfg = copy.deepcopy(_PRESETS["local"])
        else:
            cfg = _interactive_wizard(root)

    write_config(config_path, cfg)

    # Initialize store directories so the store is usable immediately.
    root_p = Path(root)
    (root_p / ".beads").mkdir(parents=True, exist_ok=True)
    (root_p / ".turns").mkdir(parents=True, exist_ok=True)

    print(f"\nWriting {config_path} ... done")
    print("Run `core-memory setup doctor` to verify your setup.\n")

    result = {
        "ok": True,
        "path": str(config_path),
        "root": str(root_p),
        "beads_dir": str(root_p / ".beads"),
        "turns_dir": str(root_p / ".turns"),
        "config": cfg,
    }
    print(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# Expanded doctor
# ---------------------------------------------------------------------------

def _storage_probe(root: str) -> dict[str, Any]:
    from pathlib import Path as P

    root_p = P(root)
    beads_dir = root_p / ".beads"
    exists = beads_dir.is_dir()
    writable = os.access(beads_dir, os.W_OK) if exists else False

    if not exists:
        return {"status": "error", "detail": "beads dir missing", "hint": f"Run: core-memory setup init --root {root}"}
    if not writable:
        return {"status": "error", "detail": "beads dir not writable", "hint": f"Check permissions on {beads_dir}"}
    return {"status": "ok", "detail": str(beads_dir)}


def _vector_probe(root: str) -> dict[str, Any]:
    try:
        from .semantic.store import SemanticStore
        ss = SemanticStore(root=root)
        size = ss.index_size() if hasattr(ss, "index_size") else None
        detail: dict[str, Any] = {"status": "ok"}
        if size is not None:
            detail["index_size"] = size
        return detail
    except ImportError:
        return {"status": "warning", "detail": "semantic store not available", "hint": "Install faiss-cpu or configure pgvector"}
    except Exception as exc:
        return {"status": "warning", "detail": str(exc)}


def _graph_probe() -> dict[str, Any]:
    try:
        from .persistence.graph import create_graph_backend
        gb = create_graph_backend()
        caps = gb.capabilities()
        if not caps.graph_traversal:
            backend_name = type(gb).__name__
            return {
                "available": False,
                "backend": backend_name,
                "status": "warning",
                "hint": "Set CORE_MEMORY_GRAPH_BACKEND=kuzu or neo4j to enable graph traversal",
            }
        backend_name = type(gb).__name__
        return {
            "available": True,
            "backend": backend_name,
            "status": "ok",
        }
    except Exception as exc:
        return {"available": False, "status": "error", "detail": str(exc)}


def _transcript_probe(root: str) -> dict[str, Any]:
    turns_dir = Path(root) / ".turns"
    if not turns_dir.exists():
        return {
            "status": "warning",
            "turns_dir": str(turns_dir),
            "hint": "No turns yet — hydration available after first session",
        }
    count = len(list(turns_dir.glob("*.jsonl"))) + len(list(turns_dir.glob("*.json")))
    return {"status": "ok", "turns_dir": str(turns_dir), "count": count}


def _dreamer_probe(root: str) -> dict[str, Any]:
    pid_file = Path(root) / ".beads" / "dreamer.pid"
    if not pid_file.exists():
        return {
            "status": "not_running",
            "hint": "Start with: core-memory dreamer start",
        }
    try:
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, 0)
            return {"status": "ok", "pid": pid}
        except ProcessLookupError:
            return {
                "status": "not_running",
                "hint": "Stale PID file — start with: core-memory dreamer start",
            }
    except Exception as exc:
        return {"status": "warning", "detail": str(exc)}


def _rolling_window_probe(root: str) -> dict[str, Any]:
    try:
        from .persistence.rolling_record_store import read_rolling_records
        t0 = time.monotonic()
        rr = read_rolling_records(root)
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        records = rr.get("records") or []
        return {
            "status": "ok",
            "record_count": len(records),
            "last_read_ms": elapsed_ms,
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


def expanded_doctor(root: str) -> dict[str, Any]:
    """Run all capability-tier probes. Returns structured report."""
    report: dict[str, Any] = {
        "storage": _storage_probe(root),
        "vector_search": _vector_probe(root),
        "graph_traversal": _graph_probe(),
        "transcript_hydration": _transcript_probe(root),
        "dreamer": _dreamer_probe(root),
        "rolling_window": _rolling_window_probe(root),
    }

    has_error = any(
        v.get("status") == "error"
        for v in report.values()
        if isinstance(v, dict)
    )
    report["ok"] = not has_error
    return report


def doctor_command(args: Any) -> None:
    root = getattr(args, "root", ".")
    report = expanded_doctor(root)
    print(json.dumps(report, indent=2))
    if not report.get("ok"):
        sys.exit(1)
