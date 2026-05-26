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
    "mode": "local",
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
    cfg, _ = load_settings_with_provenance(root)
    return cfg


def load_settings_with_provenance(
    root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (cfg, provenance) where provenance maps flat key → source label."""
    provenance: dict[str, str] = {}
    cfg = dict(_DEFAULTS)
    cfg["memory"] = dict(_DEFAULTS["memory"])

    for k in cfg:
        if k != "memory":
            provenance[k] = "default"
    for k in _DEFAULTS["memory"]:
        provenance[f"memory.{k}"] = "default"

    user_cfg = _load_yaml(_USER_CONFIG_PATH)
    if user_cfg:
        _track_provenance(user_cfg, "user-global (~/.core-memory/config.yaml)", provenance)
        cfg = _deep_merge(cfg, user_cfg)

    project_path = find_project_config(root)
    if project_path:
        project_cfg = _load_yaml(project_path)
        if project_cfg:
            label = str(project_path)
            _track_provenance(project_cfg, label, provenance)
            cfg = _deep_merge(cfg, project_cfg)

    _apply_env_overrides(cfg, provenance)
    return cfg, provenance


def _track_provenance(
    source_cfg: dict[str, Any], label: str, provenance: dict[str, str]
) -> None:
    for k, v in source_cfg.items():
        if k == "memory" and isinstance(v, dict):
            for mk in v:
                provenance[f"memory.{mk}"] = label
        else:
            provenance[k] = label


def _apply_env_overrides(
    cfg: dict[str, Any], provenance: dict[str, str] | None = None
) -> None:
    _env_map = {
        "CORE_MEMORY_BACKEND": ("backend", None),
        "CORE_MEMORY_GRAPH_BACKEND": ("graph_backend", None),
        "CORE_MEMORY_NEO4J_URI": ("neo4j", "uri"),
        "CORE_MEMORY_NEO4J_USER": ("neo4j", "username"),
        "CORE_MEMORY_NEO4J_PASSWORD": ("neo4j", "password"),
        "CORE_MEMORY_POSTGRES_DSN": ("postgres", "dsn"),
    }
    for env_var, (key, subkey) in _env_map.items():
        v = os.environ.get(env_var)
        if not v:
            continue
        if subkey is None:
            cfg[key] = v
            if provenance is not None:
                provenance[key] = f"env:{env_var}"
        else:
            cfg.setdefault(key, {})[subkey] = v
            if provenance is not None:
                provenance[f"{key}.{subkey}"] = f"env:{env_var}"


def write_config(path: Path, cfg: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False), encoding="utf-8")


def config_set(path: Path, dotted_key: str, value: str) -> None:
    """Set a dotted-key value in a YAML config file non-destructively."""
    existing = _load_yaml(path) if path.exists() else {}
    parts = dotted_key.split(".", 1)
    if len(parts) == 1:
        existing[parts[0]] = _coerce_value(value)
    else:
        existing.setdefault(parts[0], {})[parts[1]] = _coerce_value(value)
    write_config(path, existing)


def _coerce_value(v: str) -> Any:
    if v.lower() in {"true", "yes", "on"}:
        return True
    if v.lower() in {"false", "no", "off"}:
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v
