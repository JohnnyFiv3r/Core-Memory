from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.io_utils import store_lock
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


def apply_crawler_updates(
    root: str,
    session_id: str,
    updates: dict[str, Any],
    *,
    visible_bead_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Apply append-only crawler updates from agent judgments."""
    idx_file = Path(root) / ".beads" / "index.json"
    with store_lock(Path(root)):
        if not idx_file.exists():
            return {"ok": False, "error": "index_missing"}
        index = json.loads(idx_file.read_text(encoding="utf-8"))
        beads = index.get("beads") or {}
        assoc = list(index.get("associations") or [])

        session_bead_ids = {
            str((r or {}).get("id") or "")
            for r in read_session_surface(root, session_id)
            if str((r or {}).get("id") or "")
        }
        allowed_targets = set(str(x) for x in (visible_bead_ids or [])) or set(session_bead_ids)

        promotions, assoc_rows = _normalize_review_rows(updates or {})

        promoted = 0
        for bid in promotions:
            b = beads.get(str(bid))
            if not b or str(b.get("session_id") or "") != str(session_id) or str(bid) not in session_bead_ids:
                continue
            if not b.get("promotion_marked"):
                b["promotion_marked"] = True
                b["promotion_marked_at"] = datetime.now(timezone.utc).isoformat()
                b["promotion_scope"] = "rolling_continuity"
                beads[str(bid)] = b
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
            exists = any(
                a.get("source_bead") == src and a.get("target_bead") == tgt and a.get("relationship") == rel
                for a in assoc
            )
            if exists:
                continue
            assoc.append(
                {
                    "id": f"assoc-{uuid.uuid4().hex[:12].upper()}",
                    "type": "association",
                    "source_bead": src,
                    "target_bead": tgt,
                    "relationship": rel,
                    "edge_class": "agent_judged",
                    "confidence": row.get("confidence"),
                    "rationale": row.get("rationale"),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            appended += 1

        index["beads"] = beads
        index["associations"] = sorted(assoc, key=lambda a: (a.get("created_at", ""), a.get("id", "")))
        index.setdefault("stats", {})["total_associations"] = len(index["associations"])
        idx_file.write_text(json.dumps(index, indent=2), encoding="utf-8")

    return {"ok": True, "promotions_marked": promoted, "associations_appended": appended}
