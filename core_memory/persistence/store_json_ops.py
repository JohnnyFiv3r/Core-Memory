from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import atomic_write_json


def read_json_for_store(*, path: Path, root: Path, diagnostic_error_cls: type[Exception]) -> dict:
    """Read a JSON file with corruption diagnostics."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise diagnostic_error_cls(
            f"Corrupt JSON file: {path} ({exc})",
            recovery=(
                f"1. Back up the corrupt file: cp '{path}' '{path}.bak'\n"
                f"  2. Rebuild from session authority: "
                f"python -c \"from core_memory import MemoryStore; MemoryStore('{root}').rebuild_index_projection_from_sessions()\"\n"
                f"  3. If rebuild fails, delete '{path}' and re-initialize."
            ),
        ) from exc


def write_json_for_store(*, path: Path, data: dict) -> None:
    """Write JSON atomically."""
    atomic_write_json(path, data)


def normalize_enum_for_store(value: Any, enum_class: type) -> Any:
    """Normalize enum or string to string value."""
    if value is None:
        return None
    if isinstance(value, enum_class):
        return value.value
    return str(value)


__all__ = ["read_json_for_store", "write_json_for_store", "normalize_enum_for_store"]
