from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _read_int_env(name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def initialize_store_for_store(
    store: Any,
    *,
    root: str,
    tenant_id: str | None,
    backend: str,
    beads_dir_name: str = ".beads",
    turns_dir_name: str = ".turns",
) -> None:
    """Initialize store paths, runtime knobs, backend, and index bootstrap."""
    store.root = Path(root)
    store.tenant_id = tenant_id

    if tenant_id:
        store.beads_dir = store.root / beads_dir_name / "tenants" / tenant_id
        store.turns_dir = store.root / turns_dir_name / "tenants" / tenant_id
    else:
        store.beads_dir = store.root / beads_dir_name
        store.turns_dir = store.root / turns_dir_name

    store.metrics_state_file = store.beads_dir / "events" / "metrics-state.json"

    store.associate_on_add = os.environ.get("CORE_MEMORY_ASSOCIATE_ON_ADD", "1") != "0"
    store.assoc_lookback = _read_int_env("CORE_MEMORY_ASSOCIATE_LOOKBACK", 40, minimum=1)
    store.assoc_top_k = _read_int_env("CORE_MEMORY_ASSOCIATE_TOP_K", 3, minimum=0)

    store.strict_required_fields = os.environ.get("CORE_MEMORY_STRICT_REQUIRED_FIELDS", "0") == "1"
    store.bead_session_id_mode = str(os.environ.get("CORE_MEMORY_BEAD_SESSION_ID_MODE", "infer") or "infer").strip().lower()
    store.auto_promote_on_compact = os.environ.get("CORE_MEMORY_AUTO_PROMOTE_ON_COMPACT", "0") == "1"

    store.beads_dir.mkdir(parents=True, exist_ok=True)
    store.turns_dir.mkdir(parents=True, exist_ok=True)

    from core_memory.persistence.backend import create_backend

    store._backend = create_backend(store.beads_dir, backend=backend)
    store._init_index()


__all__ = ["initialize_store_for_store"]
