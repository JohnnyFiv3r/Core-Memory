from __future__ import annotations

import json
import logging
from pathlib import Path

from core_memory.persistence.rolling_record_store import read_rolling_records

logger = logging.getLogger(__name__)


AUTHORITY_RECORD_STORE = "rolling_record_store"
AUTHORITY_META_FALLBACK = "promoted_context_meta_fallback"
AUTHORITY_NONE = "none"


def load_continuity_injection(
    workspace_root: str | Path,
    max_items: int = 80,
    *,
    session_id: str | None = None,
    ensure_session_start: bool = False,
) -> dict:
    """Load runtime continuity injection context.

    Canonical authority order:
    1) rolling-window.records.json (authoritative continuity store)
    2) promoted-context.meta.json (fallback metadata only)
    3) empty

    Note: promoted-context.md is operator-facing derived text and never an
    authority surface for runtime continuity injection.
    """
    # NOTE: session-start creation is intentionally not performed here.
    # This function is read-only by design. The keyword-only params are
    # retained for adapter compatibility while callers migrate to the
    # explicit runtime boundary helper (process_session_start).
    _ = session_id
    _ = ensure_session_start

    rr = read_rolling_records(workspace_root)
    recs = list(rr.get("records") or [])
    if recs:
        return {
            "authority": AUTHORITY_RECORD_STORE,
            "records": recs[: max(1, int(max_items))],
            "included_bead_ids": list(rr.get("included_bead_ids") or []),
            "meta": dict(rr.get("meta") or {}),
        }

    meta_path = Path(workspace_root) / "promoted-context.meta.json"
    if meta_path.exists():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        return {
            "authority": AUTHORITY_META_FALLBACK,
            "records": [],
            "included_bead_ids": list(payload.get("included_bead_ids") or []),
            "meta": dict(payload.get("meta") or {}),
        }

    return {
        "authority": AUTHORITY_NONE,
        "records": [],
        "included_bead_ids": [],
        "meta": {},
    }
