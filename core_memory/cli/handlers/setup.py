"""CLI handlers for setup init, setup doctor, config commands, and demo."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from core_memory.config.settings import (
    _PROJECT_CONFIG_NAME,
    _USER_CONFIG_PATH,
    config_set,
    find_project_config,
    load_settings,
    load_settings_with_provenance,
    write_config,
)

# ---------------------------------------------------------------------------
# Mode definitions  (8b-1)
# ---------------------------------------------------------------------------

_MEM_DEFAULTS: dict[str, Any] = {
    "rolling_window_tokens": 4000,
    "max_beads": 40,
    "dreamer": True,
    "transcript_grounding": True,
}

_MODES: dict[str, dict[str, Any]] = {
    "local": {
        "mode": "local",
        "backend": "json",
        "vector_backend": "local-faiss",
        "graph_backend": "kuzu",
        "integration": "none",
        "memory": dict(_MEM_DEFAULTS),
    },
    "mcp": {
        "mode": "mcp",
        "backend": "json",
        "vector_backend": "local-faiss",
        "graph_backend": "kuzu",
        "integration": "mcp",
        "memory": dict(_MEM_DEFAULTS),
    },
    "app": {
        "mode": "app",
        "backend": "sqlite",
        "vector_backend": "local-faiss",
        "graph_backend": "kuzu",
        "integration": "none",
        "memory": dict(_MEM_DEFAULTS),
    },
    "production": {
        "mode": "production",
        "backend": "postgres",
        "vector_backend": "pgvector",
        "graph_backend": "neo4j",
        "integration": "none",
        "neo4j": {"uri": "bolt://localhost:7687", "username": "neo4j", "password": ""},
        "postgres": {"dsn": ""},
        "memory": dict(_MEM_DEFAULTS),
    },
}

# Backward-compat preset aliases — --preset still works, maps to closest mode config.
# New users should use --mode instead; --preset is a deprecated interface.
# "neo4j" is a legacy preset (json storage + neo4j graph + mcp integration) that
# predates the mode system and does not map cleanly to any current mode.
# Equivalent intent: --mode production, then override backend manually.
_PRESETS: dict[str, dict[str, Any]] = {
    "local": _MODES["local"],
    "sqlite": _MODES["app"],
    "postgres": _MODES["production"],
    "neo4j": {
        "mode": "production",
        "backend": "json",
        "vector_backend": "local-faiss",
        "graph_backend": "neo4j",
        "integration": "mcp",
        "neo4j": {"uri": "bolt://localhost:7687", "username": "neo4j", "password": ""},
        "memory": dict(_MEM_DEFAULTS),
    },
}

_MODE_CHOICES = [
    ("local",      "Local dev / trying it out   — no extra dependencies [default]"),
    ("mcp",        "MCP server                  — Claude Desktop, Cursor, etc."),
    ("app",        "App integration             — PydanticAI, OpenClaw, custom agents"),
    ("production", "Production service          — Postgres + Neo4j + pgvector"),
    ("custom",     "Custom / advanced           — configure manually"),
]


# ---------------------------------------------------------------------------
# Wizard helpers
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


def _interactive_wizard() -> dict[str, Any]:
    print("\nCore Memory setup")
    print("-" * 30)

    mode = _prompt_choice("What are you setting up Core Memory for?", _MODE_CHOICES)

    import copy
    if mode == "custom":
        cfg: dict[str, Any] = {
            "mode": "custom",
            "backend": "json",
            "vector_backend": "auto",
            "graph_backend": "kuzu",
            "integration": "none",
        }
    else:
        cfg = copy.deepcopy(_MODES.get(mode, _MODES["local"]))

    if mode == "production":
        print("\nPostgres connection")
        cfg.setdefault("postgres", {})
        dsn = _prompt("  DSN (leave blank to use CORE_MEMORY_POSTGRES_DSN env var)", "")
        if dsn:
            cfg["postgres"]["dsn"] = dsn

        print("\nNeo4j connection")
        cfg.setdefault("neo4j", {})
        cfg["neo4j"]["uri"] = _prompt("  URI", cfg["neo4j"].get("uri", "bolt://localhost:7687"))
        cfg["neo4j"]["username"] = _prompt("  Username", cfg["neo4j"].get("username", "neo4j"))
        password = _prompt("  Password (leave blank to use CORE_MEMORY_NEO4J_PASSWORD env var)", "")
        if password:
            cfg["neo4j"]["password"] = password

    print("\nMemory behavior (press Enter to accept defaults):")
    mem = cfg.setdefault("memory", dict(_MEM_DEFAULTS))
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


# ---------------------------------------------------------------------------
# init command  (8b-1)
# ---------------------------------------------------------------------------

def init_command(args: Any) -> None:
    """Handle `core-memory setup init`."""
    use_global = bool(getattr(args, "global_config", False))
    mode = getattr(args, "mode", None)
    preset = getattr(args, "preset", None)   # deprecated alias
    force = bool(getattr(args, "force", False))
    root = getattr(args, "root", ".")

    if use_global:
        config_path = _USER_CONFIG_PATH
    else:
        config_path = Path(root) / _PROJECT_CONFIG_NAME

    # Always ensure store directories exist, even when skipping config write.
    _root_p = Path(root)
    (_root_p / ".beads").mkdir(parents=True, exist_ok=True)
    (_root_p / ".turns").mkdir(parents=True, exist_ok=True)

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

    # Resolve config from mode or preset
    cfg: dict[str, Any] | None = None
    if mode:
        if mode not in _MODES and mode != "custom":
            print(json.dumps({"ok": False, "error": f"unknown mode {mode!r}; valid: {', '.join(_MODES)}"}, indent=2))
            sys.exit(1)
        import copy
        cfg = copy.deepcopy(_MODES[mode]) if mode != "custom" else {
            "mode": "custom", "backend": "json", "vector_backend": "auto",
            "graph_backend": "kuzu", "integration": "none",
        }
    elif preset:
        if preset not in _PRESETS:
            print(json.dumps({"ok": False, "error": f"unknown preset {preset!r}; valid: {', '.join(_PRESETS)}"}, indent=2))
            sys.exit(1)
        import copy
        cfg = copy.deepcopy(_PRESETS[preset])
    else:
        if not sys.stdin.isatty():
            import copy
            cfg = copy.deepcopy(_MODES["local"])
        else:
            cfg = _interactive_wizard()

    write_config(config_path, cfg)

    # Initialize store directories so the store is usable immediately.
    root_p = Path(root)
    (root_p / ".beads").mkdir(parents=True, exist_ok=True)
    (root_p / ".turns").mkdir(parents=True, exist_ok=True)

    profile = cfg.get("mode", "local")
    print(f"\nWriting {config_path} ... done")
    print(f"Run `core-memory doctor --profile {profile}` to verify your setup.\n")

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
# Doctor probes
# ---------------------------------------------------------------------------

def _storage_probe(root: str) -> dict[str, Any]:
    beads_dir = Path(root) / ".beads"
    exists = beads_dir.is_dir()
    writable = os.access(beads_dir, os.W_OK) if exists else False

    if not exists:
        return {
            "status": "error",
            "summary": "beads dir missing",
            "impact": "no memory can be written or read",
            "fix": f"core-memory setup init --root {root}",
        }
    if not writable:
        return {
            "status": "error",
            "summary": "beads dir not writable",
            "impact": "memory writes will fail",
            "fix": f"check permissions on {beads_dir}",
        }

    # For non-json backends, verify backend reachability beyond directory check.
    try:
        cfg = load_settings(root=Path(root))
        backend = cfg.get("backend", "json")

        if backend == "postgres":
            dsn = (cfg.get("postgres") or {}).get("dsn") or os.environ.get("CORE_MEMORY_POSTGRES_DSN", "")
            if not dsn:
                return {
                    "status": "error",
                    "summary": "postgres backend: no DSN configured",
                    "impact": "memory reads and writes will fail at runtime",
                    "fix": "core-memory config set postgres.dsn <dsn>  or set CORE_MEMORY_POSTGRES_DSN",
                }
            try:
                import psycopg2  # type: ignore[import-untyped]
                conn = psycopg2.connect(dsn, connect_timeout=3)
                conn.close()
                return {"status": "ok", "summary": "postgres reachable"}
            except ImportError:
                return {
                    "status": "warning",
                    "summary": "postgres DSN set; psycopg2 not installed — connectivity unverified",
                    "fix": 'pip install "core-memory[postgres]"',
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "summary": f"postgres unreachable: {exc}",
                    "impact": "memory reads and writes will fail at runtime",
                    "fix": "check postgres is running and DSN credentials are correct",
                }
    except Exception:
        pass

    # Count beads from index if it exists
    idx = beads_dir / "index.json"
    count = 0
    if idx.exists():
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            count = len((data.get("beads") or {}))
        except Exception:
            pass
    return {"status": "ok", "summary": f"{beads_dir} writable, {count} beads"}


def _vector_probe(root: str) -> dict[str, Any]:
    try:
        from core_memory.semantic.store import SemanticStore
        ss = SemanticStore(root=root)
        size = ss.index_size() if hasattr(ss, "index_size") else None
        result: dict[str, Any] = {"status": "ok", "summary": "index available"}
        if size is not None:
            result["index_size"] = size
        return result
    except ImportError:
        return {
            "status": "warning",
            "summary": "not configured",
            "impact": "semantic recall will use BM25 keyword fallback",
            "fix": 'pip install "core-memory[faiss]" then core-memory config set vector_backend local-faiss',
        }
    except Exception as exc:
        return {
            "status": "warning",
            "summary": str(exc),
            "impact": "semantic search unavailable",
            "fix": "core-memory setup init or check faiss/pgvector installation",
        }


def _graph_probe(profile: str) -> dict[str, Any]:
    """Graph probe with profile-aware framing.

    For local/mcp/app profiles, Kuzu is the embedded default — always framed as ok.
    For production profiles, Neo4j is required.
    """
    try:
        from core_memory.persistence.graph import create_graph_backend
        from core_memory.persistence.graph.null_backend import NullGraphBackend
        gb = create_graph_backend()
        caps = gb.capabilities()
        backend_name = type(gb).__name__

        if profile == "production":
            # Production requires Neo4j
            from core_memory.persistence.graph.neo4j_backend import Neo4jGraphBackend
            if not isinstance(gb, Neo4jGraphBackend):
                return {
                    "status": "error",
                    "summary": f"Neo4j not active (using {backend_name})",
                    "impact": "production causal traversal requires durable graph backend",
                    "fix": 'core-memory config set graph_backend neo4j && pip install "core-memory[neo4j]"',
                }
            if not caps.graph_traversal:
                return {
                    "status": "error",
                    "summary": "Neo4j unreachable",
                    "impact": "causal traversal will fail",
                    "fix": "verify Neo4j is running at the configured URI and credentials are correct",
                }
            return {"status": "ok", "summary": "Neo4j connected"}

        # local/mcp/app: Kuzu is the expected embedded default
        if isinstance(gb, NullGraphBackend):
            return {
                "status": "warning",
                "summary": "Kuzu unavailable, using Python fallback",
                "impact": "causal traversal uses in-memory Python walker; slower on large stores",
                "fix": 'pip install "core-memory[kuzu]"',
            }
        return {
            "status": "ok",
            "summary": f"{backend_name.replace('GraphBackend', '')} (embedded, zero-config)",
        }
    except Exception as exc:
        if profile == "production":
            return {"status": "error", "summary": str(exc), "impact": "graph unavailable", "fix": "check Neo4j config"}
        return {"status": "warning", "summary": str(exc)}


def _mcp_probe(root: str) -> dict[str, Any]:
    """Check whether any known MCP client config references core-memory."""
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", ""))

    # (client_label, config_path) — checked in order; first match wins.
    candidates = [
        # Claude Desktop — macOS
        ("Claude Desktop", home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"),
        # Claude Desktop — Linux
        ("Claude Desktop", home / ".config" / "Claude" / "claude_desktop_config.json"),
        # Claude Desktop — Windows
        ("Claude Desktop", appdata / "Claude" / "claude_desktop_config.json"),
        # Cursor — user-global (Linux/macOS)
        ("Cursor", home / ".cursor" / "mcp.json"),
        # Cursor — Windows user-global
        ("Cursor", home / "AppData" / "Roaming" / "Cursor" / "User" / "mcp.json"),
        # Cursor — workspace-local
        ("Cursor (workspace)", Path(root) / ".cursor" / "mcp.json"),
    ]

    for client, config_path in candidates:
        if not config_path.exists():
            continue
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            servers = data.get("mcpServers") or {}
            if any("core-memory" in k or "core_memory" in k for k in servers):
                return {"status": "ok", "summary": f"MCP server registered ({client})"}
        except Exception:
            pass

    return {
        "status": "error",
        "summary": "MCP server not registered in any known client config",
        "impact": "Claude / Cursor cannot connect to Core Memory tools",
        "fix": "core-memory mcp install claude",
    }


def _transcript_probe(root: str) -> dict[str, Any]:
    turns_dir = Path(root) / ".turns"
    if not turns_dir.exists():
        return {
            "status": "info",
            "summary": "no turns yet — hydration available after first session",
        }
    count = len(list(turns_dir.glob("*.jsonl"))) + len(list(turns_dir.glob("*.json")))
    return {"status": "ok", "summary": f"{count} turn file(s) in {turns_dir}"}


def _dreamer_probe(root: str) -> dict[str, Any]:
    pid_file = Path(root) / ".beads" / "dreamer.pid"
    if not pid_file.exists():
        return {"status": "info", "summary": "not running", "fix": "core-memory dreamer start"}
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return {"status": "ok", "summary": f"running (pid {pid})"}
    except (ProcessLookupError, OSError):
        return {"status": "info", "summary": "stale PID — not running", "fix": "core-memory dreamer start"}
    except Exception as exc:
        return {"status": "warning", "summary": str(exc)}


def _rolling_window_probe(root: str) -> dict[str, Any]:
    try:
        from core_memory.persistence.rolling_record_store import read_rolling_records
        t0 = time.monotonic()
        rr = read_rolling_records(root)
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        records = rr.get("records") or []
        return {"status": "ok", "summary": f"{len(records)} records", "last_read_ms": elapsed_ms}
    except Exception as exc:
        return {"status": "error", "summary": str(exc), "impact": "rolling window unavailable", "fix": "check store integrity"}


# ---------------------------------------------------------------------------
# Doctor profile severity matrix  (8b-2)
# ---------------------------------------------------------------------------

# Values: "error" | "warning" | "info" | "ok" | None (don't show)
_SEVERITY: dict[str, dict[str, str | None]] = {
    #              storage   vector      graph     mcp      transcript  dreamer   rolling_window
    "local":      ("error",  "info",     "ok",     None,    "info",     "info",   "ok"),
    "mcp":        ("error",  "info",     "ok",     "error", "info",     "info",   "ok"),
    "app":        ("error",  "warning",  "ok",     None,    "info",     "info",   "ok"),
    "production": ("error",  "error",    "error",  None,    "ok",       "warning","error"),
    "custom":     ("error",  "warning",  "warning",None,    "info",     "info",   "ok"),
}
_SEVERITY_KEYS = ("storage", "vector_search", "graph_traversal", "mcp", "transcript_hydration", "dreamer", "rolling_window")


def _profile_from_config(root: str) -> str:
    cfg = load_settings(root=Path(root))
    return str(cfg.get("mode") or "local")


def expanded_doctor(root: str, profile: str | None = None) -> dict[str, Any]:
    """Run all capability-tier probes and apply profile severity filter."""
    if profile is None:
        profile = _profile_from_config(root)

    severity_row = _SEVERITY.get(profile, _SEVERITY["local"])
    severity_map = dict(zip(_SEVERITY_KEYS, severity_row))

    raw: dict[str, Any] = {
        "storage":              _storage_probe(root),
        "vector_search":        _vector_probe(root),
        "graph_traversal":      _graph_probe(profile),
        "transcript_hydration": _transcript_probe(root),
        "dreamer":              _dreamer_probe(root),
        "rolling_window":       _rolling_window_probe(root),
    }
    # MCP probe does filesystem I/O across multiple client config paths; only run when visible.
    if severity_map.get("mcp") is not None:
        raw["mcp"] = _mcp_probe(root)

    report: dict[str, Any] = {"profile": profile}
    has_error = False

    for key in _SEVERITY_KEYS:
        max_sev = severity_map.get(key)
        if max_sev is None:
            continue  # hidden for this profile

        probe = raw[key]
        probe_status = probe.get("status", "ok")

        # Cap severity at profile max: if probe says "error" but profile caps at "warning", downgrade
        effective_status = _cap_severity(probe_status, max_sev)
        result = dict(probe)
        result["status"] = effective_status
        report[key] = result

        if effective_status == "error":
            has_error = True

    report["ok"] = not has_error
    return report


def _cap_severity(probe_status: str, max_severity: str) -> str:
    """Downgrade probe status if profile doesn't allow that severity."""
    order = {"error": 3, "warning": 2, "info": 1, "ok": 0}
    max_order = order.get(max_severity, 3)
    probe_order = order.get(probe_status, 0)
    if probe_order > max_order:
        return max_severity
    return probe_status


# ---------------------------------------------------------------------------
# Human-readable doctor formatter  (8b-2)
# ---------------------------------------------------------------------------

_STATUS_ICON = {"ok": "✓", "warning": "⚠", "error": "✗", "info": "ℹ"}
_LABEL_WIDTH = 22


def _format_doctor_human(report: dict[str, Any]) -> str:
    profile = report.get("profile", "local")
    lines = [f"Core Memory Doctor  [profile: {profile}]", ""]

    label_map = {
        "storage":              "Storage",
        "vector_search":        "Embeddings",
        "graph_traversal":      "Graph",
        "mcp":                  "MCP server",
        "transcript_hydration": "Transcripts",
        "dreamer":              "Dreamer",
        "rolling_window":       "Rolling window",
    }

    for key in _SEVERITY_KEYS:
        if key not in report:
            continue
        probe = report[key]
        status = probe.get("status", "ok")
        icon = _STATUS_ICON.get(status, "?")
        label = label_map.get(key, key)
        summary = probe.get("summary", probe.get("detail", ""))
        lines.append(f"{icon} {label:<{_LABEL_WIDTH}} {summary}")

        impact = probe.get("impact")
        fix = probe.get("fix")
        if impact:
            lines.append(f"  {'':>{_LABEL_WIDTH}}   Impact: {impact}")
        if fix:
            lines.append(f"  {'':>{_LABEL_WIDTH}}   Fix:    {fix}")

    lines.append("")
    if report.get("ok"):
        lines.append("Status: ready")
    else:
        first_fix = next(
            (report[k].get("fix") for k in _SEVERITY_KEYS if k in report and report[k].get("status") == "error" and report[k].get("fix")),
            None,
        )
        lines.append("Status: action required")
        if first_fix:
            lines.append(f"Next:   {first_fix}")

    return "\n".join(lines)


def doctor_command(args: Any) -> None:
    root = getattr(args, "root", ".")
    profile = getattr(args, "profile", None)
    as_json = bool(getattr(args, "json_output", False))

    # Auto-JSON when stdout is not a tty (scripts, CI, subprocess).
    if not as_json and not sys.stdout.isatty():
        as_json = True

    report = expanded_doctor(root, profile=profile)

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(_format_doctor_human(report))

    if not report.get("ok"):
        sys.exit(1)


# ---------------------------------------------------------------------------
# Config commands  (8b-3)
# ---------------------------------------------------------------------------

def config_show_command(args: Any) -> None:
    root = getattr(args, "root", ".")
    cfg, provenance = load_settings_with_provenance(root=Path(root))

    project_path = find_project_config(Path(root))
    user_path = _USER_CONFIG_PATH

    print("\nResolved configuration  (project-local > user-global > defaults; env vars override all)\n")

    top_keys = ["mode", "backend", "graph_backend", "vector_backend", "integration"]
    for k in top_keys:
        v = cfg.get(k, "")
        src = provenance.get(k, "default")
        print(f"  {k:<28} {str(v):<20} [{src}]")

    mem = cfg.get("memory", {})
    for k, v in mem.items():
        full_key = f"memory.{k}"
        src = provenance.get(full_key, "default")
        print(f"  {full_key:<28} {str(v):<20} [{src}]")

    print("\nSources searched:")
    print(f"  project-local:  {project_path or '(not found)'}")
    print(f"  user-global:    {user_path}{'' if user_path.exists() else '  (not found)'}")
    env_keys = [k for k in provenance.values() if k.startswith("env:")]
    if env_keys:
        print(f"  env vars:       {', '.join(set(env_keys))}")
    else:
        print("  env vars:       none relevant set")
    print()


def config_set_command(args: Any) -> None:
    root = getattr(args, "root", ".")
    key = str(getattr(args, "key", ""))
    value = str(getattr(args, "value", ""))

    config_path = Path(root) / _PROJECT_CONFIG_NAME
    config_set(config_path, key, value)
    print(json.dumps({"ok": True, "path": str(config_path), "key": key, "value": value}, indent=2))


def config_validate_command(args: Any) -> None:
    root = getattr(args, "root", ".")
    cfg = load_settings(root=Path(root))

    errors: list[str] = []
    warnings: list[str] = []

    mode = cfg.get("mode", "local")
    graph_backend = cfg.get("graph_backend", "kuzu")
    backend = cfg.get("backend", "json")
    vector_backend = cfg.get("vector_backend", "auto")

    if graph_backend == "neo4j":
        neo4j_cfg = cfg.get("neo4j") or {}
        if not neo4j_cfg.get("uri") and not os.environ.get("CORE_MEMORY_NEO4J_URI"):
            errors.append("graph_backend=neo4j but no neo4j.uri set (use CORE_MEMORY_NEO4J_URI env var)")

    if backend == "postgres":
        pg_cfg = cfg.get("postgres") or {}
        if not pg_cfg.get("dsn") and not os.environ.get("CORE_MEMORY_POSTGRES_DSN"):
            errors.append("backend=postgres but no postgres.dsn set (use CORE_MEMORY_POSTGRES_DSN env var)")

    if vector_backend == "pgvector" and backend not in {"postgres"}:
        warnings.append("vector_backend=pgvector requires backend=postgres")

    if mode == "production":
        if graph_backend != "neo4j":
            warnings.append(f"mode=production but graph_backend={graph_backend!r}; recommend neo4j for durable traversal")
        if backend not in {"postgres"}:
            warnings.append(f"mode=production but backend={backend!r}; recommend postgres for durability")

    result = {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(result, indent=2))
    if errors:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Demo command  (8b-4)
# ---------------------------------------------------------------------------

_DEMO_BEADS = [
    {
        "type": "decision",
        "title": "Chose Python over Go for the agent runtime",
        "summary": ["Python has the ML/AI ecosystem; Go has better concurrency primitives"],
        "tags": ["language", "architecture"],
    },
    {
        "type": "context",
        "title": "Project goal: evaluate causal memory systems for long-running agents",
        "summary": ["Agents lose context across sessions; causal memory preserves the why"],
        "tags": ["project", "goal"],
    },
    {
        "type": "insight",
        "title": "Go concurrency does not outweigh Python ecosystem for this use case",
        "summary": ["NumPy, transformers, faiss — all Python-first; concurrency handled via async"],
        "tags": ["language", "tradeoff"],
    },
]
_DEMO_SESSION = "__core_memory_demo__"


def demo_command(args: Any) -> None:
    root = getattr(args, "root", ".")
    keep = bool(getattr(args, "keep", False))

    from core_memory.persistence.store import MemoryStore

    print("\nCore Memory demo")
    print("-" * 30)
    print()

    memory = MemoryStore(root=root)
    written_ids: list[str] = []

    print("Writing 3 synthetic beads...")
    # add_bead is used directly here instead of emit_turn_finalized because the demo
    # operates on an ephemeral session and does not need the full write pipeline
    # (association crawl, claim extraction, promotion).
    for bead in _DEMO_BEADS:
        bead_id = memory.add_bead(
            type=bead["type"],
            title=bead["title"],
            summary=bead["summary"],
            tags=bead.get("tags"),
            session_id=_DEMO_SESSION,
        )
        written_ids.append(bead_id)
        print(f"  ✓ {bead['type']}: \"{bead['title']}\"")

    print()
    query = "why did we choose Python?"
    print(f"Recall: \"{query}\"")
    print()

    t0 = time.monotonic()
    ctx = memory.retrieve_with_context(query_text=query, limit=3)
    elapsed = round((time.monotonic() - t0) * 1000)
    results = ctx.get("results") or []

    if results:
        top = results[0]
        print(f"  Result ({elapsed}ms):")
        print(f"  {top.get('type', 'bead')}: \"{top.get('title', '')}\"")
        summary = top.get("summary") or []
        if summary:
            print(f"  → {summary[0]}")
    else:
        print(f"  (no results in {elapsed}ms — store may be empty)")

    # Graph status
    print()
    try:
        from core_memory.persistence.graph import create_graph_backend
        gb = create_graph_backend()
        caps = gb.capabilities()
        gname = type(gb).__name__.replace("GraphBackend", "")
        if caps.graph_traversal:
            print(f"  Graph: {gname} active — causal chains available")
        else:
            print(f"  Graph: {gname} (Python fallback — causal chains use in-memory walker)")
    except Exception:
        pass

    if not keep:
        print()
        print("Cleaning up demo beads...")
        _cleanup_demo(memory, written_ids)
        print("  done")

    print()
    print("Memory is working. Run `core-memory doctor` for full setup status.")
    print()


def _cleanup_demo(memory: Any, bead_ids: list[str]) -> None:
    try:
        # Remove from session file (primary source for session_id queries)
        session_file = memory.beads_dir / f"session-{_DEMO_SESSION}.jsonl"
        if session_file.exists():
            session_file.unlink()
    except Exception:
        pass
    try:
        # Also remove from index
        idx_path = memory.beads_dir / "index.json"
        index = memory._read_json(idx_path)
        beads = index.get("beads") or {}
        changed = False
        for bead_id in bead_ids:
            if bead_id in beads:
                del beads[bead_id]
                changed = True
        if changed:
            index["beads"] = beads
            memory._write_json(idx_path, index)
    except Exception:
        pass
