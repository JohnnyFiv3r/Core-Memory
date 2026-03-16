from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import append_jsonl, store_lock
from core_memory.runtime.session_surface import read_session_surface
from core_memory.persistence.store import MemoryStore
from core_memory.policy.association_contract import normalize_assoc_row, assoc_row_is_valid, assoc_dedupe_key


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

    associations_norm = [normalize_assoc_row(a) for a in associations]
    return promotions_dedup, associations_norm


def _normalize_creation_rows(updates: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [x for x in (updates.get("beads_create") or []) if isinstance(x, dict)]
    out: list[dict[str, Any]] = []
    for r in rows:
        typ = str(r.get("type") or "context").strip() or "context"
        title = str(r.get("title") or "").strip() or "Turn memory"
        summary = r.get("summary")
        if isinstance(summary, str):
            summary = [summary]
        if not isinstance(summary, list):
            summary = []
        summary = [str(x).strip() for x in summary if str(x).strip()][:5]
        if not summary:
            continue
        out.append(
            {
                "type": typ,
                "title": title[:200],
                "summary": summary,
                "tags": [str(x) for x in (r.get("tags") or []) if str(x)][:10],
                "detail": str(r.get("detail") or "")[:1200],
                "source_turn_ids": [str(x) for x in (r.get("source_turn_ids") or []) if str(x)][:5],
            }
        )
    return out


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


def merge_crawler_updates_for_flush(root: str, session_id: str) -> dict[str, Any]:
    """Flush-merge queued crawler side-log updates into index projection."""
    idx_file = Path(root) / ".beads" / "index.json"
    log_path = _crawler_updates_log_path(root, session_id)

    with store_lock(Path(root)):
        if not idx_file.exists():
            return {"ok": False, "error": "index_missing"}
        if not log_path.exists():
            return {"ok": True, "merged": 0, "promotions_marked": 0, "associations_appended": 0}

        lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        rows: list[dict[str, Any]] = []
        for ln in lines:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if isinstance(r, dict) and str(r.get("session_id") or "") == str(session_id):
                rows.append(r)

        if not rows:
            return {"ok": True, "merged": 0, "promotions_marked": 0, "associations_appended": 0}

        index = json.loads(idx_file.read_text(encoding="utf-8"))
        beads = index.get("beads") or {}
        assoc = list(index.get("associations") or [])

        promoted = 0
        appended = 0

        for row in rows:
            kind = str(row.get("kind") or "")
            if kind == "promotion_mark":
                bid = str(row.get("bead_id") or "")
                b = beads.get(bid)
                if not b:
                    continue
                if not b.get("promotion_marked"):
                    b["promotion_marked"] = True
                    b["promotion_marked_at"] = str(row.get("created_at") or datetime.now(timezone.utc).isoformat())
                    b["promotion_scope"] = str(row.get("promotion_scope") or "rolling_continuity")
                    beads[bid] = b
                    promoted += 1
            elif kind == "association_append":
                src = str(row.get("source_bead") or "")
                tgt = str(row.get("target_bead") or "")
                rel = str(row.get("relationship") or "").strip()
                if not src or not tgt or not rel:
                    continue
                if src not in beads or tgt not in beads:
                    continue
                exists = any(
                    a.get("source_bead") == src and a.get("target_bead") == tgt and a.get("relationship") == rel
                    for a in assoc
                )
                if exists:
                    continue
                assoc.append(
                    {
                        "id": str(row.get("id") or f"assoc-{uuid.uuid4().hex[:12].upper()}"),
                        "type": "association",
                        "source_bead": src,
                        "target_bead": tgt,
                        "relationship": rel,
                        "edge_class": str(row.get("edge_class") or "agent_judged"),
                        "confidence": row.get("confidence"),
                        "rationale": row.get("rationale"),
                        "created_at": str(row.get("created_at") or datetime.now(timezone.utc).isoformat()),
                    }
                )
                appended += 1

        index["beads"] = beads
        index["associations"] = sorted(assoc, key=lambda a: (a.get("created_at", ""), a.get("id", "")))
        index.setdefault("stats", {})["total_associations"] = len(index["associations"])
        idx_file.write_text(json.dumps(index, indent=2), encoding="utf-8")

        # Clear consumed side log after successful projection merge.
        log_path.write_text("", encoding="utf-8")

    return {
        "ok": True,
        "merged": len(rows),
        "promotions_marked": promoted,
        "associations_appended": appended,
        "authority_path": "flush_merge_projection",
    }


def apply_crawler_updates(
    root: str,
    session_id: str,
    updates: dict[str, Any],
    *,
    visible_bead_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Apply crawler-reviewed updates.

    Canonical semantic authority:
    - bead creation (session-local append)
    - promotion marks (queued side-log)
    - associations (queued side-log)
    """
    created = 0
    creation_rows = _normalize_creation_rows(updates or {})
    if creation_rows:
        store = MemoryStore(root)
        for row in creation_rows:
            store.add_bead(
                type=str(row.get("type") or "context"),
                title=str(row.get("title") or "Turn memory"),
                summary=list(row.get("summary") or []),
                session_id=str(session_id),
                source_turn_ids=list(row.get("source_turn_ids") or []),
                tags=list(row.get("tags") or []),
                detail=str(row.get("detail") or "") or None,
            )
            created += 1

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

        existing_assoc_keys: set[tuple[str, str, str]] = set()
        for a in (index.get("associations") or []):
            if not isinstance(a, dict):
                continue
            src0 = str(a.get("source_bead") or a.get("source_bead_id") or "")
            tgt0 = str(a.get("target_bead") or a.get("target_bead_id") or "")
            rel0 = str(a.get("relationship") or "").strip().lower()
            if src0 and tgt0 and rel0:
                existing_assoc_keys.add((src0, tgt0, rel0))

        queued_assoc_keys: set[tuple[str, str, str]] = set()
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
            row_n = normalize_assoc_row(row)
            if not assoc_row_is_valid(row_n):
                continue
            src = str(row_n.get("source_bead_id") or "")
            tgt = str(row_n.get("target_bead_id") or "")
            rel_n = str(row_n.get("relationship") or "")
            dedupe_key = assoc_dedupe_key(row_n)
            if dedupe_key in existing_assoc_keys or dedupe_key in queued_assoc_keys:
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
                    "relationship": rel_n,
                    "edge_class": "agent_judged",
                    "confidence": row.get("confidence"),
                    "rationale": row.get("rationale"),
                    "created_at": now,
                },
            )
            queued_assoc_keys.add(dedupe_key)
            appended += 1

    return {
        "ok": True,
        "beads_created": created,
        "promotions_marked": promoted,
        "associations_appended": appended,
        "queued_to": str(log_path),
        "authority_path": "session_side_log",
    }
