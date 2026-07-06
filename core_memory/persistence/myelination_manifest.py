from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

MYELINATION_MANIFEST_SCHEMA = "core_memory.myelination_manifest.v2"


def myelination_enabled() -> bool:
    raw = str(os.getenv("CORE_MEMORY_MYELINATION_ENABLED", "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def myelination_manifest_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "events" / "myelination-manifest.json"


def read_myelination_manifest(root: str | Path) -> dict[str, Any]:
    """Serve the persisted myelination manifest from disk.

    The runtime update job owns recomputing the manifest; read-side callers only
    consume the latest persisted projection.
    """
    p = myelination_manifest_path(root)
    if not p.exists():
        return {
            "ok": True,
            "present": False,
            "schema": MYELINATION_MANIFEST_SCHEMA,
            "enabled": myelination_enabled(),
            "note": "no myelination manifest yet; run a myelination-update to build it",
        }
    try:
        manifest = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(manifest, dict):
            return {"ok": True, "present": True, **manifest}
    except Exception:
        pass
    return {
        "ok": False,
        "present": False,
        "error": "myelination_manifest_unreadable",
        "schema": MYELINATION_MANIFEST_SCHEMA,
    }


def myelination_edge_key(src: str, dst: str, rel: str) -> str:
    return f"{src}|{rel}|{dst}"


def myelination_edge_key_parts(key: str) -> tuple[str, str, str]:
    src, rel, dst = (str(key or "").split("|", 2) + ["", "", ""])[:3]
    return src, rel, dst


def _read_bonus_map(root: str | Path, field: str) -> dict[str, float]:
    payload = read_myelination_manifest(root)
    if not bool(payload.get("present")):
        return {}
    out: dict[str, float] = {}
    for key, value in (payload.get(field) or {}).items():
        try:
            bonus = float(value)
        except Exception:
            continue
        if abs(bonus) > 1e-9:
            out[str(key)] = bonus
    return out


def read_myelination_edge_bonus_map(root: str | Path) -> dict[str, float]:
    return _read_bonus_map(root, "bonus_by_edge_key")


def read_myelination_bead_bonus_map(root: str | Path) -> dict[str, float]:
    return _read_bonus_map(root, "bonus_by_bead_id")


__all__ = [
    "MYELINATION_MANIFEST_SCHEMA",
    "myelination_edge_key",
    "myelination_edge_key_parts",
    "myelination_enabled",
    "myelination_manifest_path",
    "read_myelination_bead_bonus_map",
    "read_myelination_edge_bonus_map",
    "read_myelination_manifest",
]
