"""Core Memory project/user config file reader.

Reads `.core-memory.yaml` (project-local, walks up from cwd) and
`~/.core-memory/config.yaml` (user-global), merges with env vars.

Precedence (lowest → highest): shipped defaults < user-global < project-local < env vars.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


_DEFAULTS: dict[str, Any] = {
    "backend": "json",
    "vector_backend": "auto",
    "graph_backend": "kuzu",
    "integration": "none",
    "memory": {
        "rolling_window_tokens": 4000,
        "max_beads": 40,
        "dreamer": True,
        "transcript_grounding": True,
    },
}

_PROJECT_CONFIG_NAME = ".core-memory.yaml"
_USER_CONFIG_PATH = Path.home() / ".core-memory" / "config.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def find_project_config(start: Path | None = None) -> Path | None:
    """Walk up from start (default: cwd) looking for .core-memory.yaml."""
    cwd = start or Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / _PROJECT_CONFIG_NAME
        if candidate.exists():
            return candidate
    return None


def load_settings(root: Path | None = None) -> dict[str, Any]:
    """Return merged settings dict: defaults < user-global < project-local < env vars."""
    cfg = dict(_DEFAULTS)
    cfg["memory"] = dict(_DEFAULTS["memory"])

    user_cfg = _load_yaml(_USER_CONFIG_PATH)
    if user_cfg:
        cfg = _deep_merge(cfg, user_cfg)

    project_path = find_project_config(root)
    if project_path:
        project_cfg = _load_yaml(project_path)
        if project_cfg:
            cfg = _deep_merge(cfg, project_cfg)

    # env var overrides
    _apply_env_overrides(cfg)
    return cfg


def _apply_env_overrides(cfg: dict[str, Any]) -> None:
    if v := os.environ.get("CORE_MEMORY_BACKEND"):
        cfg["backend"] = v
    if v := os.environ.get("CORE_MEMORY_GRAPH_BACKEND"):
        cfg["graph_backend"] = v
    if v := os.environ.get("CORE_MEMORY_NEO4J_URI"):
        cfg.setdefault("neo4j", {})["uri"] = v
    if v := os.environ.get("CORE_MEMORY_NEO4J_USER"):
        cfg.setdefault("neo4j", {})["username"] = v
    if v := os.environ.get("CORE_MEMORY_NEO4J_PASSWORD"):
        cfg.setdefault("neo4j", {})["password"] = v
    if v := os.environ.get("CORE_MEMORY_POSTGRES_DSN"):
        cfg.setdefault("postgres", {})["dsn"] = v


def write_config(path: Path, cfg: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False), encoding="utf-8")
