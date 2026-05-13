from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

SCHEMA = "session_enrichment_delta.v1"
NORMALIZER_VERSION = "session_enrichment_delta.normalizer.slice_a.1"

_MAX_ROWS = {
    "beads_create": 4,
    "promotions": 64,
    "associations": 256,
    "association_lifecycle": 128,
    "entity_upserts": 128,
    "claims": 128,
    "claim_updates": 128,
    "goal_lifecycle": 64,
    "memory_outcomes": 8,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_str(value: Any) -> str:
    return str(value or "").strip()


def _str_list(values: Any, *, limit: int | None = None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        s = _as_str(value)
        if s:
            out.append(s)
            if limit is not None and len(out) >= limit:
                break
    return out


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _ctx_fingerprint(ctx: dict[str, Any]) -> str:
    basis = {
        "session_id": _as_str(ctx.get("session_id")),
        "visible_bead_ids": _str_list(ctx.get("visible_bead_ids")),
        "window_turn_ids": _str_list(ctx.get("window_turn_ids")),
        "carry_in_bead_ids": _str_list(ctx.get("carry_in_bead_ids")),
    }
    if ctx.get("context_fingerprint"):
        return _as_str(ctx.get("context_fingerprint"))
    return f"sha256:{stable_hash(basis)}"


def build_window_context_ref(
    *,
    session_id: str,
    crawler_ctx: dict[str, Any] | None = None,
    window_turn_ids: list[str] | None = None,
    carry_in_bead_ids: list[str] | None = None,
    selection_reason: str = "turn_finalization",
    limit: int = 200,
) -> dict[str, Any]:
    """Build explicit bounded window metadata for a session enrichment judgment."""
    ctx = dict(crawler_ctx or {})
    beads = [b for b in (ctx.get("beads") or []) if isinstance(b, dict)]
    visible = _str_list(ctx.get("visible_bead_ids"))
    if not visible:
        visible = _str_list((b or {}).get("id") for b in beads)
    turns_from_beads: list[str] = []
    for bead in beads:
        turns_from_beads.extend(_str_list(bead.get("source_turn_ids")))
    window_turns = _str_list(window_turn_ids or turns_from_beads)
    carry_ids = _str_list(carry_in_bead_ids or ctx.get("carry_in_bead_ids"))
    ref = {
        "session_id": _as_str(session_id or ctx.get("session_id")),
        "selection_reason": _as_str(selection_reason) or "turn_finalization",
        "limit": int(limit),
        "row_count": len(beads),
        "first_visible_bead_id": visible[0] if visible else None,
        "last_visible_bead_id": visible[-1] if visible else None,
        "first_visible_turn_id": window_turns[0] if window_turns else None,
        "last_visible_turn_id": window_turns[-1] if window_turns else None,
        "visible_bead_ids": visible,
        "window_turn_ids": window_turns,
        "carry_in_bead_ids": carry_ids,
        "carry_in_reasons": {bid: "explicit_input" for bid in carry_ids},
    }
    ref["context_fingerprint"] = _ctx_fingerprint(ref)
    return ref


def _base_row(
    *,
    dedupe_key: str,
    confidence: Any = 0.8,
    provenance_kind: str = "model_inferred",
    source: str = "crawler_updates",
    bead_id: str | None = None,
    turn_id: str | None = None,
    evidence_refs: Any = None,
    context_fingerprint: str,
    sequence_key: str | None = None,
    rationale: str | None = None,
) -> dict[str, Any]:
    try:
        conf = float(confidence)
    except Exception:
        conf = 0.8
    conf = max(0.0, min(1.0, conf))
    refs = [r for r in (evidence_refs or []) if isinstance(r, dict)]
    return {
        "dedupe_key": dedupe_key,
        "confidence": conf,
        "provenance": {
            "kind": _as_str(provenance_kind) or "model_inferred",
            "source": _as_str(source) or "crawler_updates",
            "bead_id": bead_id or None,
            "turn_id": turn_id or None,
        },
        "evidence_refs": refs,
        "context_fingerprint": context_fingerprint,
        "sequence_key": sequence_key,
        "rationale": rationale,
    }


def _quarantine(row_type: str, row: Any, reasons: list[str], *, session_id: str, turn_id: str) -> dict[str, Any]:
    return {
        "schema": "session_enrichment_delta.quarantine.v1",
        "delta_schema": SCHEMA,
        "session_id": _as_str(session_id),
        "turn_id": _as_str(turn_id),
        "row_type": row_type,
        "dedupe_key": row.get("dedupe_key") if isinstance(row, dict) else None,
        "reasons": list(reasons),
        "warnings": [],
        "normalized_record": row if isinstance(row, dict) else {},
        "original_record": row,
        "created_at": _now(),
    }


def _bounded(
    rows: list[Any],
    row_type: str,
    *,
    session_id: str,
    turn_id: str,
) -> tuple[list[Any], list[dict[str, Any]]]:
    max_rows = int(_MAX_ROWS[row_type])
    accepted = rows[:max_rows]
    quarantined = [
        _quarantine(row_type, row, ["array_bound_exceeded"], session_id=session_id, turn_id=turn_id)
        for row in rows[max_rows:]
    ]
    return accepted, quarantined


def _row_provenance_kind(row: dict[str, Any]) -> Any:
    provenance = row.get("provenance")
    if isinstance(provenance, dict):
        return provenance.get("kind")
    return provenance


def crawler_updates_to_delta(
    *,
    session_id: str,
    turn_id: str,
    updates: dict[str, Any] | None,
    crawler_ctx: dict[str, Any] | None = None,
    source_kind: str = "inline",
    authority_path: str = "canonical_in_process",
    origin: str = "USER_TURN",
    idempotency_key: str | None = None,
    window_turn_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Normalize current crawler updates into the Slice A delta envelope.

    This is intentionally an adapter, not a semantic policy engine. It preserves
    existing crawler payload fields and adds explicit refs/dedupe metadata.
    """
    sid = _as_str(session_id)
    tid = _as_str(turn_id)
    raw = dict(updates or {})
    window_ref = build_window_context_ref(
        session_id=sid,
        crawler_ctx=crawler_ctx,
        window_turn_ids=window_turn_ids,
        selection_reason="queued_enrichment" if source_kind == "queued" else "turn_finalization",
    )
    ctx_fp = _as_str(window_ref.get("context_fingerprint"))
    quarantine_rows: list[dict[str, Any]] = []

    delta: dict[str, Any] = {
        "schema": SCHEMA,
        "session_id": sid,
        "turn_id": tid,
        "source": {
            "kind": _as_str(source_kind) or "inline",
            "authority_path": _as_str(authority_path) or "canonical_in_process",
            "origin": _as_str(origin) or "USER_TURN",
            "queue_id": None,
            "idempotency_key": idempotency_key,
        },
        "contract_versions": {
            "delta": SCHEMA,
            "rubric": "current-runtime",
            "prompt": "current-runtime",
            "model": "current-runtime",
            "normalizer": NORMALIZER_VERSION,
        },
        "window_context_ref": window_ref,
        "provenance": {
            "producer": "core_memory.runtime",
            "run_id": None,
            "trace_id": None,
            "transaction_id": None,
            "created_at": _now(),
        },
        "beads_create": [],
        "promotions": [],
        "associations": [],
        "association_lifecycle": [],
        "entity_upserts": [],
        "claims": [],
        "claim_updates": [],
        "goal_lifecycle": [],
        "memory_outcomes": [],
        "diagnostics": {},
    }

    bead_rows, q = _bounded(
        [r for r in raw.get("beads_create") or [] if isinstance(r, dict)],
        "beads_create",
        session_id=sid,
        turn_id=tid,
    )
    quarantine_rows.extend(q)
    for row in bead_rows:
        src_turns = _str_list(row.get("source_turn_ids"))
        basis = {
            "session_id": sid,
            "turn_id": tid,
            "type": _as_str(row.get("type") or "context"),
            "title": _as_str(row.get("title")),
            "summary": _str_list(row.get("summary")),
            "source_turn_ids": src_turns,
        }
        out = dict(row)
        out.update(
            _base_row(
                dedupe_key=f"bead:{sid}:{tid}:{basis['type']}:{stable_hash(basis)[:16]}",
                confidence=row.get("confidence", 0.8),
                provenance_kind=_as_str(row.get("provenance")) or "model_inferred",
                source="crawler_updates.beads_create",
                turn_id=tid,
                evidence_refs=row.get("evidence_refs") or [],
                context_fingerprint=ctx_fp,
                rationale=_as_str(row.get("rationale") or row.get("detail")) or None,
            )
        )
        if tid and tid not in src_turns:
            out.setdefault("warnings", []).append("current_turn_id_missing_from_source_turn_ids")
        delta["beads_create"].append(out)

    promotions_raw = _str_list(raw.get("promotions"))
    for reviewed in raw.get("reviewed_beads") or []:
        if not isinstance(reviewed, dict):
            continue
        bid = _as_str(reviewed.get("bead_id"))
        state = _as_str(reviewed.get("promotion_state")).lower()
        if bid and state in {"promote", "promoted", "preserve_full_in_rolling", "mark_promoted"}:
            promotions_raw.append(bid)
    promotions_raw, q = _bounded(promotions_raw, "promotions", session_id=sid, turn_id=tid)
    quarantine_rows.extend(q)
    seen_promotions: set[str] = set()
    for bid in promotions_raw:
        key = f"promotion:{sid}:{bid}:rolling_continuity"
        if key in seen_promotions:
            continue
        seen_promotions.add(key)
        row = {
            "bead_id": bid,
            "promotion_scope": "rolling_continuity",
            "desired_state": "marked",
            "reason_text": None,
        }
        row.update(
            _base_row(
                dedupe_key=key,
                context_fingerprint=ctx_fp,
                source="crawler_updates.promotions",
                bead_id=bid,
                turn_id=tid,
            )
        )
        delta["promotions"].append(row)

    assoc_rows = [r for r in raw.get("associations") or [] if isinstance(r, dict)]
    for reviewed in raw.get("reviewed_beads") or []:
        if not isinstance(reviewed, dict):
            continue
        bid = _as_str(reviewed.get("bead_id"))
        for assoc in reviewed.get("associations") or []:
            if isinstance(assoc, dict):
                a = dict(assoc)
                a.setdefault("source_bead_id", bid)
                assoc_rows.append(a)
    assoc_rows, q = _bounded(assoc_rows, "associations", session_id=sid, turn_id=tid)
    quarantine_rows.extend(q)
    for row in assoc_rows:
        src = _as_str(row.get("source_bead") or row.get("source_bead_id"))
        tgt = _as_str(row.get("target_bead") or row.get("target_bead_id"))
        rel = _as_str(row.get("relationship")).lower()
        if not src or not tgt or not rel:
            quarantine_rows.append(
                _quarantine(
                    "associations",
                    row,
                    ["missing_source_target_or_relationship"],
                    session_id=sid,
                    turn_id=tid,
                )
            )
            continue
        out = {
            "source_bead_id": src,
            "target_bead_id": tgt,
            "relationship": rel,
            "relationship_raw": _as_str(row.get("relationship_raw")) or None,
            "reason_text": _as_str(row.get("reason_text") or row.get("rationale")),
            "reason_code": _as_str(row.get("reason_code")) or None,
            "evidence_fields": _str_list(row.get("evidence_fields")),
            "edge_class": _as_str(row.get("edge_class")) or "agent_judged",
        }
        out.update(
            _base_row(
                dedupe_key=f"assoc:{src}:{tgt}:{rel}",
                confidence=row.get("confidence", 0.8),
                provenance_kind=_as_str(row.get("provenance")) or "model_inferred",
                source="crawler_updates.associations",
                bead_id=src,
                turn_id=tid,
                evidence_refs=row.get("evidence_refs") or [],
                context_fingerprint=ctx_fp,
                rationale=_as_str(row.get("rationale") or row.get("reason_text")) or None,
            )
        )
        delta["associations"].append(out)

    life_rows = [r for r in raw.get("association_lifecycle") or [] if isinstance(r, dict)]
    for reviewed in raw.get("reviewed_beads") or []:
        if not isinstance(reviewed, dict):
            continue
        for action in reviewed.get("association_actions") or []:
            if isinstance(action, dict):
                life_rows.append(action)
    life_rows, q = _bounded(life_rows, "association_lifecycle", session_id=sid, turn_id=tid)
    quarantine_rows.extend(q)
    for i, row in enumerate(life_rows):
        aid = _as_str(row.get("association_id"))
        action = _as_str(row.get("action")).lower()
        if not aid or action not in {"retract", "supersede", "reaffirm"}:
            quarantine_rows.append(
                _quarantine(
                    "association_lifecycle",
                    row,
                    ["invalid_lifecycle_action"],
                    session_id=sid,
                    turn_id=tid,
                )
            )
            continue
        replacement = _as_str(row.get("replacement_association_id")) or None
        sequence_key = f"assoc-life:{sid}:{tid}:{i}"
        out = {
            "association_id": aid,
            "action": action,
            "replacement_association_id": replacement,
            "reason_text": _as_str(row.get("reason_text") or row.get("reason")) or None,
        }
        out.update(
            _base_row(
                dedupe_key=f"assoc-life:{aid}:{action}:{replacement or 'null'}:{sequence_key}",
                confidence=row.get("confidence", 0.8),
                provenance_kind=_as_str(row.get("provenance")) or "model_inferred",
                source="crawler_updates.association_lifecycle",
                turn_id=tid,
                context_fingerprint=ctx_fp,
                sequence_key=sequence_key,
                rationale=_as_str(row.get("reason_text") or row.get("reason")) or None,
            )
        )
        delta["association_lifecycle"].append(out)

    delta["diagnostics"] = {
        "quarantined": len(quarantine_rows),
        "quarantine": quarantine_rows,
        "input_keys": sorted(str(k) for k in raw.keys()),
    }
    return delta


def delta_to_crawler_updates(delta: dict[str, Any]) -> dict[str, Any]:
    """Project accepted Slice A rows back to the current crawler update shape."""
    out: dict[str, Any] = {
        "beads_create": [],
        "promotions": [],
        "associations": [],
        "association_lifecycle": [],
    }
    for row in delta.get("beads_create") or []:
        if isinstance(row, dict):
            adapter_keys = {
                "dedupe_key",
                "confidence",
                "provenance",
                "context_fingerprint",
                "sequence_key",
                "rationale",
            }
            out["beads_create"].append(
                {k: v for k, v in row.items() if k not in adapter_keys}
            )
    for row in delta.get("promotions") or []:
        if isinstance(row, dict) and row.get("bead_id"):
            out["promotions"].append(_as_str(row.get("bead_id")))
    for row in delta.get("associations") or []:
        if isinstance(row, dict):
            out["associations"].append(
                {
                    "source_bead_id": _as_str(row.get("source_bead_id")),
                    "target_bead_id": _as_str(row.get("target_bead_id")),
                    "relationship": _as_str(row.get("relationship")),
                    "reason_text": _as_str(row.get("reason_text")),
                    "confidence": row.get("confidence"),
                    "provenance": _row_provenance_kind(row),
                    "reason_code": row.get("reason_code"),
                    "evidence_fields": list(row.get("evidence_fields") or []),
                    "relationship_raw": row.get("relationship_raw"),
                    "rationale": row.get("rationale"),
                }
            )
    for row in delta.get("association_lifecycle") or []:
        if isinstance(row, dict):
            out["association_lifecycle"].append(
                {
                    "association_id": _as_str(row.get("association_id")),
                    "action": _as_str(row.get("action")),
                    "replacement_association_id": row.get("replacement_association_id"),
                    "reason_text": row.get("reason_text"),
                    "confidence": row.get("confidence"),
                    "provenance": _row_provenance_kind(row),
                }
            )
    return out


__all__ = [
    "SCHEMA",
    "NORMALIZER_VERSION",
    "build_window_context_ref",
    "crawler_updates_to_delta",
    "delta_to_crawler_updates",
    "stable_hash",
]
