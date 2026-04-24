from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import append_jsonl, store_lock
from core_memory.runtime.session_surface import read_session_surface
from core_memory.persistence.store import MemoryStore
from core_memory.association.quarantine import write_quarantine
from core_memory.policy.association_contract import assoc_dedupe_key
from core_memory.policy.association_inference_v21 import (
    INFERENCE_MODE_PERMISSIVE,
    INFERENCE_MODE_STRICT,
    validate_and_normalize_inference_payload,
)
from core_memory.policy.hygiene import enforce_bead_hygiene_contract, can_be_retrieval_eligible, rewrite_generic_title


def _normalize_review_rows(updates: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """Accept normalized crawler payload shapes."""
    promotions = [str(x) for x in (updates.get("promotions") or []) if str(x)]
    associations = [x for x in (updates.get("associations") or []) if isinstance(x, dict)]
    lifecycle_rows = [x for x in (updates.get("association_lifecycle") or []) if isinstance(x, dict)]

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
                        "reason_text": a.get("reason_text"),
                        "rationale": a.get("rationale"),
                        "provenance": a.get("provenance"),
                        "reason_code": a.get("reason_code"),
                        "evidence_fields": a.get("evidence_fields"),
                        "relationship_raw": a.get("relationship_raw"),
                    }
                )

        for act in (row.get("association_actions") or []):
            if isinstance(act, dict):
                lifecycle_rows.append(
                    {
                        "association_id": str(act.get("association_id") or "").strip(),
                        "action": str(act.get("action") or "").strip().lower(),
                        "replacement_association_id": str(act.get("replacement_association_id") or "").strip(),
                        "reason_text": str(act.get("reason_text") or act.get("reason") or "").strip(),
                        "confidence": act.get("confidence"),
                        "provenance": str(act.get("provenance") or "model_inferred").strip().lower() or "model_inferred",
                    }
                )

    # de-dup preserving order
    seen = set()
    promotions_dedup = []
    for p in promotions:
        if p not in seen:
            promotions_dedup.append(p)
            seen.add(p)

    associations_norm: list[dict[str, Any]] = []
    for a in associations:
        associations_norm.append(
            {
                "source_bead": str(a.get("source_bead") or a.get("source_bead_id") or "").strip(),
                "target_bead": str(a.get("target_bead") or a.get("target_bead_id") or "").strip(),
                "relationship": str(a.get("relationship") or "").strip().lower(),
                "reason_text": str(a.get("reason_text") or "").strip(),
                "rationale": str(a.get("rationale") or "").strip(),
                "confidence": a.get("confidence"),
                "provenance": str(a.get("provenance") or "model_inferred").strip().lower() or "model_inferred",
                "reason_code": a.get("reason_code"),
                "evidence_fields": list(a.get("evidence_fields") or []),
                "relationship_raw": str(a.get("relationship_raw") or "").strip().lower(),
            }
        )

    lifecycle_norm: list[dict[str, Any]] = []
    for row in lifecycle_rows:
        lifecycle_norm.append(
            {
                "association_id": str(row.get("association_id") or "").strip(),
                "action": str(row.get("action") or "").strip().lower(),
                "replacement_association_id": str(row.get("replacement_association_id") or "").strip(),
                "reason_text": str(row.get("reason_text") or row.get("reason") or "").strip(),
                "confidence": row.get("confidence"),
                "provenance": str(row.get("provenance") or "model_inferred").strip().lower() or "model_inferred",
            }
        )
    return promotions_dedup, associations_norm, lifecycle_norm


def _normalize_creation_rows(updates: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [x for x in (updates.get("beads_create") or []) if isinstance(x, dict)]
    out: list[dict[str, Any]] = []
    for r in rows:
        typ = str(r.get("type") or "context").strip() or "context"
        title = rewrite_generic_title(str(r.get("title") or "").strip() or "assistant turn")[:200]

        summary = r.get("summary")
        if isinstance(summary, str):
            summary = [summary]
        if not isinstance(summary, list):
            summary = []
        summary = [str(x).strip() for x in summary if str(x).strip()][:5]

        row = {
            "type": typ,
            "title": title,
            "summary": summary,  # optional by contract
            "tags": [str(x) for x in (r.get("tags") or []) if str(x)][:10],
            "detail": str(r.get("detail") or "")[:1200],
            "source_turn_ids": [str(x) for x in (r.get("source_turn_ids") or []) if str(x)][:5],
            "session_id": str(r.get("session_id") or "") or None,
            "turn_index": r.get("turn_index"),
            "prev_bead_id": str(r.get("prev_bead_id") or "") or None,
            "retrieval_eligible": bool(r.get("retrieval_eligible", False)),
            "retrieval_title": str(r.get("retrieval_title") or "")[:200] or None,
            "retrieval_facts": [str(x) for x in (r.get("retrieval_facts") or []) if str(x)][:12],
            "entities": [str(x) for x in (r.get("entities") or []) if str(x)][:20],
            "topics": [str(x) for x in (r.get("topics") or []) if str(x)][:20],
            "validity": str(r.get("validity") or "")[:40] or None,
            "because": [str(x) for x in (r.get("because") or []) if str(x)][:8],
            "supporting_facts": [str(x) for x in (r.get("supporting_facts") or []) if str(x)][:12],
            "evidence_refs": [str(x) for x in (r.get("evidence_refs") or []) if str(x)][:12],
            "state_change": r.get("state_change") if isinstance(r.get("state_change"), dict) else None,
            "effective_from": str(r.get("effective_from") or "") or None,
            "effective_to": str(r.get("effective_to") or "") or None,
            "observed_at": str(r.get("observed_at") or "") or None,
            "supersedes": [str(x) for x in (r.get("supersedes") or []) if str(x)][:8],
            "superseded_by": [str(x) for x in (r.get("superseded_by") or []) if str(x)][:8],
        }

        # Retrieval eligibility is payload-gated; downgrade rather than reject row.
        if row.get("retrieval_eligible") and not can_be_retrieval_eligible(row):
            row["retrieval_eligible"] = False

        row = enforce_bead_hygiene_contract(row)

        # Temporal minimum: source_turn_ids required for creation rows.
        if not row.get("source_turn_ids"):
            continue
        out.append(row)
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
        "writing_contract": [
            "A bead is the canonical record for a turn; every turn must produce one bead.",
            "Thin beads preserve temporal continuity; rich beads carry structured retrieval payload.",
            "Summary is optional; do not invent prose when structured fields are stronger.",
            "Initial write requires temporal grounding only (session/turn order/prev bead).",
            "Do not force broad causal or semantic links on initial write unless strongly grounded.",
            "After the first turn in a session, look actively for non-temporal semantic associations to prior visible beads.",
            "When multiple relationships are highly plausibly true, append all of them rather than choosing only one.",
            "High plausibility is enough for append-only associations; certainty is not required, but do not invent links unsupported by the visible record.",
            "Prefer specific semantic links like supports, refines, caused_by, enables, diagnoses, resolves, supersedes, or contradicts over generic or purely temporal links when the turn meaningfully updates prior memory.",
            "If no non-temporal semantic link is strongly or highly plausibly supported, omit it rather than fabricating one.",
        ],
        "retrieval_contract": [
            "retrieval_eligible=true requires structured payload (retrieval_title + retrieval_facts + quality signals).",
            "If payload is weak, downgrade to retrieval_eligible=false rather than failing creation.",
        ],
        "allowed_updates": {
            "beads_create": "list[{type,title,source_turn_ids,turn_index?,prev_bead_id?,retrieval_eligible?,retrieval_title?,retrieval_facts?,entities?,topics?,validity?,because?,supporting_facts?,evidence_refs?,state_change?,effective_from?,effective_to?,observed_at?,supersedes?,superseded_by?,summary?,detail?,tags?}]",
            "reviewed_beads": "list[{bead_id,promotion_state,reason?,associations?}]",
            "associations": "list[{source_bead_id,target_bead_id,relationship,reason_text,confidence,provenance?,reason_code?,evidence_fields?,relationship_raw?,rationale?}]",
        },
        "append_only_rules": [
            "promotion_marked can only be set true and means preserve_full_in_rolling semantics",
            "associations are append-only records",
            "source must be session-local bead",
            "target must be in visible_bead_ids set",
            "initial-write minimum association is temporal only; richer associations may be appended later",
            "for non-initial turns, review the candidate relationship list against each plausible target and append every high-plausibility semantic relation that applies",
            "do not collapse distinct plausible relations into one weak default if multiple stronger relations are justified by the visible evidence",
        ],
    }


def _crawler_updates_log_path(root: str, session_id: str) -> Path:
    sid = str(session_id or "main").strip() or "main"
    sid = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in sid)
    return Path(root) / ".beads" / "events" / f"crawler-updates-{sid}.jsonl"


def merge_crawler_updates(root: str, session_id: str) -> dict[str, Any]:
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

        strict_default = os.environ.get("CORE_MEMORY_ASSOC_STRICT", "1") != "0"
        inference_mode = INFERENCE_MODE_STRICT if strict_default else INFERENCE_MODE_PERMISSIVE
        quarantine_path = Path(root) / ".beads" / "events" / "association-quarantine.jsonl"

        promoted = 0
        appended = 0
        quarantined = 0
        lifecycle_applied = 0
        lifecycle_rejected = 0

        session_bead_ids = {
            bid for bid, bead in beads.items() if str((bead or {}).get("session_id") or "") == str(session_id)
        }

        def _association_in_session_scope(assoc_row: dict[str, Any]) -> bool:
            src0 = str((assoc_row or {}).get("source_bead") or (assoc_row or {}).get("source_bead_id") or "")
            tgt0 = str((assoc_row or {}).get("target_bead") or (assoc_row or {}).get("target_bead_id") or "")
            return bool(src0 and tgt0 and src0 in session_bead_ids and tgt0 in session_bead_ids)

        assoc_by_id: dict[str, dict[str, Any]] = {}
        for a in assoc:
            if isinstance(a, dict):
                aid0 = str(a.get("id") or "")
                if aid0:
                    assoc_by_id[aid0] = a

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

                validated = validate_and_normalize_inference_payload(
                    {
                        "source_bead": src,
                        "target_bead": tgt,
                        "relationship": rel,
                        "reason_text": str(row.get("reason_text") or ""),
                        "confidence": row.get("confidence"),
                        "provenance": str(row.get("provenance") or "model_inferred"),
                        "reason_code": row.get("reason_code"),
                        "evidence_fields": list(row.get("evidence_fields") or []),
                        "relationship_raw": str(row.get("relationship_raw") or ""),
                    },
                    mode=inference_mode,
                )
                row_n = validated.record
                if not validated.ok:
                    write_quarantine(
                        Path(root),
                        row_n,
                        reasons=list(validated.quarantine_reasons),
                        warnings=list(validated.warnings),
                        original_payload=row,
                        session_id=str(session_id),
                    )
                    quarantined += 1
                    continue

                src = str(row_n.get("source_bead") or "")
                tgt = str(row_n.get("target_bead") or "")
                rel = str(row_n.get("relationship") or "")
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
                        "status": "active",
                        "edge_class": str(row.get("edge_class") or "agent_judged"),
                        "confidence": row_n.get("confidence"),
                        "reason_text": row_n.get("reason_text"),
                        "rationale": row_n.get("reason_text"),
                        "provenance": row_n.get("provenance") or "model_inferred",
                        "relationship_raw": row_n.get("relationship_raw"),
                        "warnings": list(row_n.get("warnings") or []),
                        "reason_code": row_n.get("reason_code"),
                        "evidence_fields": list(row_n.get("evidence_fields") or []),
                        "normalization_applied": bool(row_n.get("normalization_applied", False)),
                        "created_at": str(row.get("created_at") or datetime.now(timezone.utc).isoformat()),
                    }
                )
                assoc_by_id[str(assoc[-1].get("id") or "")] = assoc[-1]
                appended += 1
            elif kind == "association_lifecycle":
                aid = str(row.get("association_id") or "").strip()
                action = str(row.get("action") or "").strip().lower()
                if not aid or action not in {"retract", "supersede", "reaffirm"}:
                    continue
                target = assoc_by_id.get(aid)
                if not isinstance(target, dict):
                    continue

                if not _association_in_session_scope(target):
                    lifecycle_rejected += 1
                    continue

                now = str(row.get("created_at") or datetime.now(timezone.utc).isoformat())
                reason_text = str(row.get("reason_text") or "").strip()
                provenance = str(row.get("provenance") or "model_inferred").strip().lower() or "model_inferred"

                if action == "retract":
                    target["status"] = "retracted"
                    target["retracted_at"] = now
                    if reason_text:
                        target["lifecycle_reason"] = reason_text
                    target["lifecycle_provenance"] = provenance
                    lifecycle_applied += 1
                elif action == "reaffirm":
                    target["status"] = "active"
                    target["reaffirmed_at"] = now
                    if row.get("confidence") is not None:
                        try:
                            conf = float(row.get("confidence"))
                            old = target.get("confidence")
                            target["confidence"] = max(float(old), conf) if old is not None else conf
                        except Exception:
                            pass
                    if reason_text:
                        target["lifecycle_reason"] = reason_text
                    target["lifecycle_provenance"] = provenance
                    lifecycle_applied += 1
                elif action == "supersede":
                    replacement_id = str(row.get("replacement_association_id") or "").strip()
                    if replacement_id:
                        replacement = assoc_by_id.get(replacement_id)
                        if not isinstance(replacement, dict) or (not _association_in_session_scope(replacement)):
                            lifecycle_rejected += 1
                            continue
                    target["status"] = "superseded"
                    target["superseded_at"] = now
                    if replacement_id:
                        target["superseded_by_association_id"] = replacement_id
                        if isinstance(replacement, dict):
                            replacement["status"] = "active"
                            replacement["supersedes_association_id"] = aid
                    if reason_text:
                        target["lifecycle_reason"] = reason_text
                    target["lifecycle_provenance"] = provenance
                    lifecycle_applied += 1

        for a in assoc:
            if isinstance(a, dict) and not str(a.get("status") or "").strip():
                a["status"] = "active"

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
        "association_lifecycle_applied": lifecycle_applied,
        "association_lifecycle_rejected": lifecycle_rejected,
        "associations_quarantined": quarantined,
        "quarantine_path": str(quarantine_path),
        "authority_path": "merge_projection",
    }


def merge_crawler_updates_for_flush(root: str, session_id: str) -> dict[str, Any]:
    """Compatibility wrapper for legacy flush-named callsites.

    Canonical behavior is neutral merge via `merge_crawler_updates(...)`.
    """
    out = merge_crawler_updates(root=root, session_id=session_id)
    if isinstance(out, dict):
        out["authority_path"] = "flush_merge_projection"
    return out


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
                title=str(row.get("title") or "assistant turn"),
                summary=list(row.get("summary") or []),
                session_id=str(session_id),
                source_turn_ids=list(row.get("source_turn_ids") or []),
                tags=list(row.get("tags") or []),
                detail=str(row.get("detail") or "") or None,
                retrieval_eligible=bool(row.get("retrieval_eligible", False)),
                retrieval_title=row.get("retrieval_title"),
                retrieval_facts=list(row.get("retrieval_facts") or []),
                entities=list(row.get("entities") or []),
                topics=list(row.get("topics") or []),
                validity=row.get("validity"),
                because=list(row.get("because") or []),
                supporting_facts=list(row.get("supporting_facts") or []),
                evidence_refs=list(row.get("evidence_refs") or []),
                state_change=row.get("state_change"),
                effective_from=row.get("effective_from"),
                effective_to=row.get("effective_to"),
                observed_at=row.get("observed_at"),
                supersedes=list(row.get("supersedes") or []),
                superseded_by=list(row.get("superseded_by") or []),
                prev_bead_id=row.get("prev_bead_id"),
                turn_index=row.get("turn_index"),
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
        association_scope = str((updates or {}).get("association_scope") or "").strip().lower()
        # default: keep target scope to visible set for turn-time safety
        # enrichment mode: allow wider session-historical linking
        if association_scope == "historical_session":
            allowed_targets = set(session_bead_ids)
        else:
            allowed_targets = set(str(x) for x in (visible_bead_ids or [])) or set(session_bead_ids)

        promotions, assoc_rows, lifecycle_rows = _normalize_review_rows(updates or {})
        now = datetime.now(timezone.utc).isoformat()
        log_path = _crawler_updates_log_path(root, session_id)
        strict_default = os.environ.get("CORE_MEMORY_ASSOC_STRICT", "1") != "0"
        inference_mode = INFERENCE_MODE_STRICT if strict_default else INFERENCE_MODE_PERMISSIVE
        quarantine_path = Path(root) / ".beads" / "events" / "association-quarantine.jsonl"

        existing_assoc_keys: set[tuple[str, str, str]] = set()
        existing_assoc_by_id: dict[str, dict[str, Any]] = {}
        for a in (index.get("associations") or []):
            if not isinstance(a, dict):
                continue
            src0 = str(a.get("source_bead") or a.get("source_bead_id") or "")
            tgt0 = str(a.get("target_bead") or a.get("target_bead_id") or "")
            rel0 = str(a.get("relationship") or "").strip().lower()
            if src0 and tgt0 and rel0:
                existing_assoc_keys.add((src0, tgt0, rel0))
            aid0 = str(a.get("id") or "")
            if aid0:
                existing_assoc_by_id[aid0] = a

        queued_assoc_keys: set[tuple[str, str, str]] = set()
        quarantined = 0
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
        lifecycle_queued = 0
        lifecycle_rejected = 0
        for row in assoc_rows:
            if not isinstance(row, dict):
                continue
            validated = validate_and_normalize_inference_payload(row, mode=inference_mode)
            row_n = validated.record

            if not validated.ok:
                write_quarantine(
                    Path(root),
                    row_n,
                    reasons=list(validated.quarantine_reasons),
                    warnings=list(validated.warnings),
                    original_payload=row,
                    session_id=str(session_id),
                )
                quarantined += 1
                continue
            src = str(row_n.get("source_bead") or "")
            tgt = str(row_n.get("target_bead") or "")
            rel_n = str(row_n.get("relationship") or "")
            dedupe_key = assoc_dedupe_key(
                {
                    "source_bead_id": src,
                    "target_bead_id": tgt,
                    "relationship": rel_n,
                }
            )
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
                    "confidence": row_n.get("confidence"),
                    "reason_text": row_n.get("reason_text"),
                    "rationale": row_n.get("reason_text"),
                    "provenance": row_n.get("provenance") or "model_inferred",
                    "relationship_raw": row_n.get("relationship_raw"),
                    "warnings": list(row_n.get("warnings") or []),
                    "reason_code": row_n.get("reason_code"),
                    "evidence_fields": list(row_n.get("evidence_fields") or []),
                    "normalization_applied": bool(row_n.get("normalization_applied", False)),
                    "created_at": now,
                },
            )
            queued_assoc_keys.add(dedupe_key)
            appended += 1

        for row in lifecycle_rows:
            aid = str(row.get("association_id") or "").strip()
            action = str(row.get("action") or "").strip().lower()
            if not aid or action not in {"retract", "supersede", "reaffirm"}:
                continue

            target = existing_assoc_by_id.get(aid)
            if not isinstance(target, dict):
                lifecycle_rejected += 1
                continue

            src = str(target.get("source_bead") or target.get("source_bead_id") or "")
            tgt = str(target.get("target_bead") or target.get("target_bead_id") or "")
            sb = beads.get(src)
            tb = beads.get(tgt)
            if not sb or not tb:
                lifecycle_rejected += 1
                continue
            if str(sb.get("session_id") or "") != str(session_id) or str(tb.get("session_id") or "") != str(session_id):
                lifecycle_rejected += 1
                continue
            if src not in session_bead_ids:
                lifecycle_rejected += 1
                continue
            if tgt not in allowed_targets:
                lifecycle_rejected += 1
                continue

            replacement_id = str(row.get("replacement_association_id") or "").strip()
            if action == "supersede" and replacement_id:
                replacement = existing_assoc_by_id.get(replacement_id)
                if not isinstance(replacement, dict):
                    lifecycle_rejected += 1
                    continue
                rs = str(replacement.get("source_bead") or replacement.get("source_bead_id") or "")
                rt = str(replacement.get("target_bead") or replacement.get("target_bead_id") or "")
                rb_s = beads.get(rs)
                rb_t = beads.get(rt)
                if not rb_s or not rb_t:
                    lifecycle_rejected += 1
                    continue
                if str(rb_s.get("session_id") or "") != str(session_id) or str(rb_t.get("session_id") or "") != str(session_id):
                    lifecycle_rejected += 1
                    continue

            append_jsonl(
                log_path,
                {
                    "schema": "openclaw.memory.crawler_update.v1",
                    "kind": "association_lifecycle",
                    "session_id": str(session_id),
                    "association_id": aid,
                    "action": action,
                    "replacement_association_id": str(row.get("replacement_association_id") or "") or None,
                    "reason_text": str(row.get("reason_text") or "") or None,
                    "confidence": row.get("confidence"),
                    "provenance": str(row.get("provenance") or "model_inferred"),
                    "created_at": now,
                },
            )
            lifecycle_queued += 1

    return {
        "ok": True,
        "beads_created": created,
        "promotions_marked": promoted,
        "associations_appended": appended,
        "association_lifecycle_queued": lifecycle_queued,
        "association_lifecycle_rejected": lifecycle_rejected,
        "associations_quarantined": quarantined,
        "quarantine_path": str(quarantine_path),
        "queued_to": str(log_path),
        "authority_path": "session_side_log",
    }
