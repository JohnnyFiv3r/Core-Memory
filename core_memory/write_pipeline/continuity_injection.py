from __future__ import annotations

import json
import logging
from pathlib import Path

from core_memory.persistence.rolling_record_store import read_rolling_records
from core_memory.persistence.store import MemoryStore

logger = logging.getLogger(__name__)


AUTHORITY_RECORD_STORE = "rolling_record_store"
AUTHORITY_META_FALLBACK = "promoted_context_meta_fallback"
AUTHORITY_NONE = "none"


def _ensure_session_start_marker(workspace_root: str | Path, session_id: str) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return

    store = MemoryStore(str(workspace_root))
    try:
        index = store._read_json(store.beads_dir / "index.json")
        beads = index.get("beads") or {}
        for b in beads.values():
            if str((b or {}).get("session_id") or "") != sid:
                continue
            tags = {str(t) for t in ((b or {}).get("tags") or [])}
            if "session_start" in tags:
                return

        store.add_bead(
            type="lifecycle",
            title="Session start boundary",
            summary=["Session-start lifecycle boundary"],
            session_id=sid,
            source_turn_ids=[f"session-start:{sid}"],
            tags=["session_start", "lifecycle_boundary"],
            retrieval_eligible=False,
            detail="Lifecycle marker generated when continuity is requested with ensure_session_start enabled.",
        )
    finally:
        store.close()


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
    if ensure_session_start and session_id:
        try:
            _ensure_session_start_marker(workspace_root, session_id)
        except Exception as exc:
            logger.debug("continuity.session_start_marker_failed: %s", exc)

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
