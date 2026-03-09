from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.io_utils import append_jsonl, store_lock
from core_memory.session_surface import read_session_surface


def _normalize_review_rows(updates: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """Accept both legacy and structured crawler payload shapes."""
    promotions = [str(x) for x in (updates.get("promotions") or []) if str(x)]
    associations = [x for x in (updates.get("associations") or []) if isinstance(x, dict)]

    reviewed = [x for x in (updates.get("reviewed_beads") or []) if isinstance(x, dict)]
    for row in reviewed:
        bid = str(row.get("bead_id") or "")
        state = str(row.get("promotion_state") or "").strip().lower()
        if bid and state in {"promote", "promoted", "preserve_full_in_rolling", "mark_promoted"}:
            promotions.append(bid)
        for a in (row.get("associations") or []):
            if isinstance(a, dict):
                associations.append(
                    {
                        "source_bead_id": str(a.get("source_bead_id") or bid or ""),
                        "target_bead_id": str(a.get("target_bead_id") or ""),
                        "relationship": str(a.get("relationship") or ""),
                        "confidence": a.get("confidence"),
                        "rationale": a.get("rationale"),
                    }
                )

    # de-dup preserving order
    seen = set()
    promotions_dedup = []
    for p in promotions:
        if p not in seen:
            promotions_dedup.append(p)
            seen.add(p)

    return promotions_dedup, associations


def build_crawler_context(root: str, session_id: str, limit: int = 200, carry_in_bead_ids: list[str] | None = None) -> dict[str, Any]:
    """Provide bounded session-scoped context for agent-judged crawler decisions."""
    rows = read_session_surface(root, session_id)
    rows = rows[-max(1, int(limit)) :]
    session_ids = [str((r or {}).get("id") or "") for r in rows if str((r or {}).get("id") or "")]
    carry_ids = [str(x) for x in (carry_in_bead_ids or []) if str(x)]
    visible_set = sorted(set(session_ids + carry_ids))

    return {
        "session_id": session_id,
        "beads": rows,
        "visible_bead_ids": visible_set,
        "allowed_updates": {
            "reviewed_beads": "list[{bead_id,promotion_state,reason?,associations?}]",
            "associations": "list[{source_bead_id,target_bead_id,relationship,confidence?,rationale?}]",
        },
        "append_only_rules": [
            "promotion_marked can only be set true and means preserve_full_in_rolling semantics",
            "associations are append-only records",
            "source must be session-local bead",
            "target must be in visible_bead_ids set",
        ],
    }


def _crawler_updates_log_path(root: str, session_id: str) -> Path:
    sid = str(session_id or "main").strip() or "main"
    sid = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in sid)
    return Path(root) / ".beads" / "events" / f"crawler-updates-{sid}.jsonl"


def apply_crawler_updates(
    root: str,
    session_id: str,
    updates: dict[str, Any],
    *,
    visible_bead_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Queue append-only crawler updates into a session-local side log.

    P8A Step 3: crawler-applied updates are no longer written directly to index.json.
    They are validated and appended to a session-local side log for later flush-merge.
    """
    idx_file = Path(root) / ".beads" / "index.json"
    with store_lock(Path(root)):
        if not idx_file.exists():
            return {"ok": False, "error": "index_missing"}
        index = json.loads(idx_file.read_text(encoding="utf-8"))
        beads = index.get("beads") or {}

        session_bead_ids = {
            str((r or {}).get("id") or "")
            for r in read_session_surface(root, session_id)
            if str((r or {}).get("id") or "")
        }
        allowed_targets = set(str(x) for x in (visible_bead_ids or [])) or set(session_bead_ids)

        promotions, assoc_rows = _normalize_review_rows(updates or {})
        now = datetime.now(timezone.utc).isoformat()
        log_path = _crawler_updates_log_path(root, session_id)

        promoted = 0
        for bid in promotions:
            b = beads.get(str(bid))
            if not b or str(b.get("session_id") or "") != str(session_id) or str(bid) not in session_bead_ids:
                continue
            append_jsonl(
                log_path,
                {
                    "schema": "openclaw.memory.crawler_update.v1",
                    "kind": "promotion_mark",
                    "session_id": str(session_id),
                    "bead_id": str(bid),
                    "promotion_scope": "rolling_continuity",
                    "created_at": now,
                },
            )
            promoted += 1

        appended = 0
        for row in assoc_rows:
            if not isinstance(row, dict):
                continue
            src = str(row.get("source_bead_id") or "")
            tgt = str(row.get("target_bead_id") or "")
            rel = str(row.get("relationship") or "").strip()
            if not src or not tgt or not rel:
                continue
            sb = beads.get(src)
            tb = beads.get(tgt)
            if not sb or not tb:
                continue
            if str(sb.get("session_id") or "") != str(session_id):
                continue
            if src not in session_bead_ids:
                continue
            if tgt not in allowed_targets:
                continue
            append_jsonl(
                log_path,
                {
                    "schema": "openclaw.memory.crawler_update.v1",
                    "kind": "association_append",
                    "session_id": str(session_id),
                    "id": f"assoc-{uuid.uuid4().hex[:12].upper()}",
                    "source_bead": src,
                    "target_bead": tgt,
                    "relationship": rel,
                    "edge_class": "agent_judged",
                    "confidence": row.get("confidence"),
                    "rationale": row.get("rationale"),
                    "created_at": now,
                },
            )
            appended += 1

    return {
        "ok": True,
        "promotions_marked": promoted,
        "associations_appended": appended,
        "queued_to": str(log_path),
        "authority_path": "session_side_log",
    }
