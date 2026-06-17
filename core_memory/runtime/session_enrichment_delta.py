from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import store_lock
from core_memory.schema.normalization import (
    INFERENCE_CANONICAL_RELATION_TYPES,
    canonicalize_association_edge,
)

SCHEMA = "session_enrichment_delta.v1"
NORMALIZER_VERSION = "session_enrichment_delta.normalizer.slice_a.1"
CANONICAL_DELTA_RELATIONSHIPS = set(INFERENCE_CANONICAL_RELATION_TYPES)
CURRENT_TURN_ASSOC_SOURCE_ALIASES = {
    "__current_turn__",
    "current_turn",
    "$current_turn",
    "@current_turn",
}
DELTA_QUARANTINE_PATH = ".beads/events/session-enrichment-delta-quarantine.jsonl"

DELTA_ROW_LIMITS = {
    "beads_create": 4,
    "promotions": 64,
    "associations": 256,
    "association_lifecycle": 128,
    "entity_upserts": 0,  # reserved; Slice A does not process these rows
    "claims": 0,  # reserved; Slice A does not process these rows
    "claim_updates": 0,  # reserved; Slice A does not process these rows
    "goal_lifecycle": 0,  # reserved; Slice A does not process these rows
    "memory_outcomes": 0,  # reserved; Slice A does not process these rows
}
DELTA_ROW_TYPES = tuple(DELTA_ROW_LIMITS.keys())

_VOLATILE_BEAD_FIELDS = {
    "id",
    "created_at",
    "updated_at",
    "last_recalled",
    "promotion_decided_at",
    "promoted_at",
    "promotion_marked_at",
    "association_preview",
    "entity_ids",
    "prev_bead_id",
    "next_bead_id",
    "type_log",
    "type_coerced_from",
    "validation_warnings",
}

_VOLATILE_ASSOC_FIELDS = {
    "id",
    "created_at",
    "updated_at",
    "retracted_at",
    "reaffirmed_at",
    "superseded_at",
    "lifecycle_updated_at",
    "warnings",
    "normalization_applied",
}

_VOLATILE_ENTITY_FIELDS = {"created_at", "updated_at"}


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


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def _strip_keys(row: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {str(k): v for k, v in row.items() if str(k) not in keys}


def _normalize_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return sorted(value, key=lambda x: _canonical_json(x))


def _stable_bead_key(row: dict[str, Any]) -> str:
    source_turn_ids = _normalize_list(_str_list(row.get("source_turn_ids")))
    basis = {
        "session_id": _as_str(row.get("session_id")),
        "source_turn_ids": source_turn_ids,
        "type": _as_str(row.get("type")),
        "title": _as_str(row.get("title")),
        "summary": _normalize_list(row.get("summary")),
        "detail": _as_str(row.get("detail")),
        "tags": _normalize_list(row.get("tags")),
    }
    turn_part = "+".join(str(x) for x in source_turn_ids) or "no-turn"
    return f"bead:{basis['session_id']}:{turn_part}:{basis['type']}:{stable_hash(basis)[:16]}"


def _stable_claim_key(row: dict[str, Any], bead_id_map: dict[str, str]) -> str:
    source_bead_id = _as_str(row.get("source_bead_id"))
    source_bead_key = bead_id_map.get(source_bead_id, source_bead_id)
    basis = {
        "subject": _as_str(row.get("subject")).lower(),
        "slot": _as_str(row.get("slot")).lower(),
        "value": row.get("value"),
        "source_bead_key": source_bead_key,
        "source_turn_ids": _normalize_list(_str_list(row.get("source_turn_ids"))),
    }
    return f"claim:{basis['subject']}:{basis['slot']}:{stable_hash(basis)[:16]}"


def _normalize_claim_row(row: dict[str, Any], bead_id_map: dict[str, str]) -> dict[str, Any]:
    normalized = _strip_keys(row, {"id", "created_at", "updated_at"})
    source_bead_id = _as_str(normalized.get("source_bead_id"))
    if source_bead_id:
        normalized["source_bead_key"] = bead_id_map.get(source_bead_id, source_bead_id)
        normalized.pop("source_bead_id", None)
    if "source_turn_ids" in normalized:
        normalized["source_turn_ids"] = _normalize_list(_str_list(normalized.get("source_turn_ids")))
    normalized["dedupe_key"] = _stable_claim_key(row, bead_id_map)
    return normalized


def _normalize_claim_update_row(
    row: dict[str, Any],
    *,
    bead_id_map: dict[str, str],
    claim_id_map: dict[str, str],
) -> dict[str, Any]:
    normalized = _strip_keys(row, {"id", "created_at", "updated_at"})
    trigger_bead_id = _as_str(normalized.get("trigger_bead_id"))
    if trigger_bead_id:
        normalized["trigger_bead_key"] = bead_id_map.get(trigger_bead_id, trigger_bead_id)
        normalized.pop("trigger_bead_id", None)

    target_claim_id = _as_str(normalized.get("target_claim_id"))
    replacement_claim_id = _as_str(normalized.get("replacement_claim_id"))
    if target_claim_id:
        normalized["target_claim_key"] = claim_id_map.get(target_claim_id, target_claim_id)
        normalized.pop("target_claim_id", None)
    if replacement_claim_id:
        normalized["replacement_claim_key"] = claim_id_map.get(replacement_claim_id, replacement_claim_id)
        normalized.pop("replacement_claim_id", None)

    basis = {
        "decision": _as_str(normalized.get("decision")).lower(),
        "target_claim_key": normalized.get("target_claim_key"),
        "replacement_claim_key": normalized.get("replacement_claim_key"),
        "subject": _as_str(normalized.get("subject")).lower(),
        "slot": _as_str(normalized.get("slot")).lower(),
        "trigger_bead_key": normalized.get("trigger_bead_key"),
        "reason_text": _as_str(normalized.get("reason_text")),
    }
    normalized["dedupe_key"] = f"claim-update:{stable_hash(basis)[:20]}"
    return normalized
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


def _normalize_evidence_refs(
    evidence_refs: Any,
    *,
    context_fingerprint: str,
    provenance_kind: str,
) -> list[dict[str, Any]]:
    refs = [r for r in (evidence_refs or []) if isinstance(r, dict)]
    if refs or _as_str(provenance_kind) in {"fallback", "test"}:
        return refs
    return [
        {
            "kind": "context_fingerprint",
            "id": context_fingerprint,
            "field": None,
            "quote": None,
            "hash": context_fingerprint,
        }
    ]


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
    prov_kind = _as_str(provenance_kind) or "model_inferred"
    refs = _normalize_evidence_refs(
        evidence_refs,
        context_fingerprint=context_fingerprint,
        provenance_kind=prov_kind,
    )
    return {
        "dedupe_key": dedupe_key,
        "confidence": conf,
        "provenance": {
            "kind": prov_kind,
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


def _delta_quarantine_key(row: dict[str, Any]) -> str:
    basis = {
        "session_id": _as_str(row.get("session_id")),
        "turn_id": _as_str(row.get("turn_id")),
        "row_type": _as_str(row.get("row_type")),
        "row_dedupe_key": _as_str(row.get("dedupe_key")),
        "reasons": _normalize_list(row.get("reasons") or []),
        "original_record": row.get("original_record"),
    }
    return stable_hash(basis)


def _merge_unique_strs(existing: Any, incoming: Any) -> list[str]:
    out: list[str] = []
    for value in list(existing or []) + list(incoming or []):
        text = _as_str(value)
        if text and text not in out:
            out.append(text)
    return out


def write_delta_quarantine(root: str | Path, delta_or_rows: dict[str, Any] | list[Any]) -> dict[str, Any]:
    """Persist delta quarantine diagnostics to the Slice A quarantine JSONL surface.

    Rows are deduped by stable semantic quarantine content so replaying the same
    enrichment job increments `seen_count` instead of appending duplicates.
    """
    if isinstance(delta_or_rows, dict):
        diagnostics = delta_or_rows.get("diagnostics") or {}
        raw_rows = diagnostics.get("quarantine") or []
    else:
        raw_rows = delta_or_rows
    rows = [r for r in (raw_rows or []) if isinstance(r, dict)]
    path = Path(root) / DELTA_QUARANTINE_PATH
    if not rows:
        return {"ok": True, "path": str(path), "quarantine_count": 0, "written": 0, "deduped": 0}

    with store_lock(Path(root)):
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_rows: list[dict[str, Any]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    existing_rows.append(parsed)

        by_key = {str(row.get("quarantine_dedupe_key") or ""): row for row in existing_rows}
        now = _now()
        written = 0
        deduped = 0
        for row in rows:
            quarantine_key = _delta_quarantine_key(row)
            existing = by_key.get(quarantine_key)
            if existing is not None:
                existing["seen_count"] = int(existing.get("seen_count") or 1) + 1
                existing["last_seen_at"] = now
                existing["reasons"] = _merge_unique_strs(existing.get("reasons"), row.get("reasons"))
                existing["warnings"] = _merge_unique_strs(existing.get("warnings"), row.get("warnings"))
                deduped += 1
                continue

            stored = dict(row)
            stored.setdefault("schema", "session_enrichment_delta.quarantine.v1")
            stored.setdefault("delta_schema", SCHEMA)
            stored["quarantine_dedupe_key"] = quarantine_key
            stored.setdefault("created_at", now)
            stored["last_seen_at"] = now
            stored["seen_count"] = 1
            existing_rows.append(stored)
            by_key[quarantine_key] = stored
            written += 1

        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in existing_rows) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "path": str(path),
        "quarantine_count": len(rows),
        "written": written,
        "deduped": deduped,
    }


def _bounded(
    rows: list[Any],
    row_type: str,
    *,
    session_id: str,
    turn_id: str,
) -> tuple[list[Any], list[dict[str, Any]]]:
    max_rows = int(DELTA_ROW_LIMITS[row_type])
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
    visible_bead_ids = set(_str_list(window_ref.get("visible_bead_ids")))
    association_scope = _as_str(raw.get("association_scope")).lower()
    historical_association_scope = association_scope == "historical_session"
    source_kind_norm = _as_str(source_kind) or "inline"
    default_provenance_kind = source_kind_norm if source_kind_norm in {"fallback", "test"} else "model_inferred"
    quarantine_rows: list[dict[str, Any]] = []

    delta: dict[str, Any] = {
        "schema": SCHEMA,
        "session_id": sid,
        "turn_id": tid,
        "source": {
            "kind": source_kind_norm,
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
    if historical_association_scope:
        delta["association_scope"] = "historical_session"

    bead_rows, q = _bounded(
        [r for r in raw.get("beads_create") or [] if isinstance(r, dict)],
        "beads_create",
        session_id=sid,
        turn_id=tid,
    )
    quarantine_rows.extend(q)
    entity_candidates: list[tuple[dict[str, Any], str | None, str]] = []
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
                provenance_kind=_as_str(row.get("provenance")) or default_provenance_kind,
                source="crawler_updates.beads_create",
                turn_id=tid,
                evidence_refs=row.get("evidence_refs") or [],
                context_fingerprint=ctx_fp,
                rationale=_as_str(row.get("rationale") or row.get("detail")) or None,
            )
        )
        bead_dedupe_key = str(out.get("dedupe_key") or "") or None
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
                provenance_kind=default_provenance_kind,
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
        rel_raw = _as_str(row.get("relationship")).lower()
        edge = canonicalize_association_edge(src, tgt, rel_raw)
        src = _as_str(edge.get("source_bead"))
        tgt = _as_str(edge.get("target_bead"))
        rel = _as_str(edge.get("relationship"))
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
        if rel not in CANONICAL_DELTA_RELATIONSHIPS:
            quarantined = dict(row)
            quarantined["relationship_raw"] = rel_raw
            quarantine_rows.append(
                _quarantine(
                    "associations",
                    quarantined,
                    [f"noncanonical_relationship:{rel_raw or 'empty'}"],
                    session_id=sid,
                    turn_id=tid,
                )
            )
            continue
        visibility_reasons: list[str] = []
        source_is_current_turn_alias = src.lower() in CURRENT_TURN_ASSOC_SOURCE_ALIASES
        target_is_current_turn_alias = tgt.lower() in CURRENT_TURN_ASSOC_SOURCE_ALIASES
        if src not in visible_bead_ids and not historical_association_scope and not source_is_current_turn_alias:
            visibility_reasons.append("source_outside_visible_window")
        if tgt not in visible_bead_ids and not historical_association_scope and not target_is_current_turn_alias:
            visibility_reasons.append("target_outside_visible_window")
        if visibility_reasons:
            quarantine_rows.append(
                _quarantine(
                    "associations",
                    row,
                    visibility_reasons,
                    session_id=sid,
                    turn_id=tid,
                )
            )
            continue
        out = {
            "source_bead_id": src,
            "target_bead_id": tgt,
            "relationship": rel,
            "relationship_raw": _as_str(row.get("relationship_raw")) or (rel_raw if edge.get("normalization_applied") else None),
            "endpoints_swapped": True if edge.get("endpoints_swapped") else None,
            "reason_text": _as_str(row.get("reason_text") or row.get("rationale")),
            "reason_code": _as_str(row.get("reason_code")) or None,
            "evidence_fields": _str_list(row.get("evidence_fields")),
            "edge_class": _as_str(row.get("edge_class")) or "agent_judged",
        }
        out.update(
            _base_row(
                dedupe_key=f"assoc:{src}:{tgt}:{rel}",
                confidence=row.get("confidence", 0.8),
                provenance_kind=_as_str(row.get("provenance")) or default_provenance_kind,
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
                provenance_kind=_as_str(row.get("provenance")) or default_provenance_kind,
                source="crawler_updates.association_lifecycle",
                turn_id=tid,
                context_fingerprint=ctx_fp,
                sequence_key=sequence_key,
                rationale=_as_str(row.get("reason_text") or row.get("reason")) or None,
            )
        )
        delta["association_lifecycle"].append(out)

    reserved_row_types = ("entity_upserts", "claims", "claim_updates", "goal_lifecycle", "memory_outcomes")
    reserved_input_counts = {
        row_type: len([r for r in raw.get(row_type) or [] if isinstance(r, dict)])
        for row_type in reserved_row_types
    }

    accepted_counts = {row_type: len(delta.get(row_type) or []) for row_type in DELTA_ROW_TYPES}
    quarantined_counts = {row_type: 0 for row_type in DELTA_ROW_TYPES}
    for qrow in quarantine_rows:
        row_type = _as_str((qrow or {}).get("row_type"))
        if row_type in quarantined_counts:
            quarantined_counts[row_type] += 1
    delta["diagnostics"] = {
        "quarantined": len(quarantine_rows),
        "quarantine": quarantine_rows,
        "input_keys": sorted(str(k) for k in raw.keys()),
        "row_limits": dict(DELTA_ROW_LIMITS),
        "accepted_counts": accepted_counts,
        "quarantined_counts": quarantined_counts,
        "reserved_input_counts": reserved_input_counts,
    }
    return delta


def _has_only_default_context_ref(row: dict[str, Any]) -> bool:
    refs = row.get("evidence_refs")
    if not isinstance(refs, list) or len(refs) != 1 or not isinstance(refs[0], dict):
        return False
    ref = refs[0]
    ctx = _as_str(row.get("context_fingerprint"))
    return (
        _as_str(ref.get("kind")) == "context_fingerprint"
        and _as_str(ref.get("id")) == ctx
        and _as_str(ref.get("hash")) == ctx
        and ref.get("field") is None
        and ref.get("quote") is None
    )


def _strip_adapter_keys_for_projection(row: dict[str, Any], adapter_keys: set[str]) -> dict[str, Any]:
    keys = set(adapter_keys)
    if _has_only_default_context_ref(row):
        keys.add("evidence_refs")
    return {k: v for k, v in row.items() if k not in keys}


def delta_to_crawler_updates(delta: dict[str, Any]) -> dict[str, Any]:
    """Project accepted Slice A rows back to the current crawler update shape."""
    out: dict[str, Any] = {
        "beads_create": [],
        "promotions": [],
        "associations": [],
        "association_lifecycle": [],
        "claims": [],
        "claim_updates": [],
        "goal_lifecycle": [],
        "memory_outcomes": [],
    }
    if _as_str(delta.get("association_scope")).lower() == "historical_session":
        out["association_scope"] = "historical_session"
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
            out["beads_create"].append(_strip_adapter_keys_for_projection(row, adapter_keys))
    for row in delta.get("promotions") or []:
        if isinstance(row, dict) and row.get("bead_id"):
            out["promotions"].append(_as_str(row.get("bead_id")))
    for row in delta.get("associations") or []:
        if isinstance(row, dict):
            projected_assoc = {
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
            if row.get("evidence_refs") and not _has_only_default_context_ref(row):
                projected_assoc["evidence_refs"] = list(row.get("evidence_refs") or [])
            out["associations"].append(projected_assoc)
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
    # Future delta rows are reserved for later slices. Slice A projection only
    # carries beads, promotions, associations, and association lifecycle rows.
    return out


def canonical_session_projection(root: str | Path, session_id: str) -> dict[str, Any]:
    """Build a stable committed-state projection for Slice A equality checks.

    This helper intentionally ignores volatile ids/timestamps and queue metadata.
    It reads the canonical committed surfaces only; it does not mutate runtime
    state or make semantic decisions.
    """
    from core_memory.runtime.session_surface import read_session_surface

    root_path = Path(root)
    sid = _as_str(session_id)
    index = _read_json(root_path / ".beads" / "index.json", {})
    if not isinstance(index, dict):
        index = {}

    session_rows = [r for r in read_session_surface(root_path, sid) if isinstance(r, dict)]
    visible_ids = _str_list((r or {}).get("id") for r in session_rows)

    raw_beads = {
        _as_str(bid): row
        for bid, row in (index.get("beads") or {}).items()
        if isinstance(row, dict) and _as_str(row.get("session_id")) == sid
    }
    bead_id_map = {bid: _stable_bead_key(row) for bid, row in raw_beads.items()}
    visible_keys = [bead_id_map.get(bid, bid) for bid in visible_ids]
    visible_set = set(visible_ids) | set(visible_keys)
    session_entity_ids = {
        _as_str(eid)
        for row in raw_beads.values()
        for eid in (row.get("entity_ids") or [])
        if _as_str(eid)
    }

    claim_id_map: dict[str, str] = {}
    for bid, row in raw_beads.items():
        for claim in row.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            cid = _as_str(claim.get("id"))
            if cid:
                claim_with_source = dict(claim)
                claim_with_source.setdefault("source_bead_id", bid)
                claim_id_map[cid] = _stable_claim_key(claim_with_source, bead_id_map)

    beads: dict[str, Any] = {}
    for bid, row in raw_beads.items():
        bead_key = bead_id_map[bid]
        normalized = _strip_keys(row, _VOLATILE_BEAD_FIELDS)
        normalized["stable_bead_key"] = bead_key
        for key in ("summary", "because", "tags", "entities", "topics", "source_turn_ids"):
            if key in normalized:
                normalized[key] = _normalize_list(normalized.get(key))
        if "claims" in normalized:
            normalized["claims"] = _normalize_list(
                [
                    _normalize_claim_row({**claim, "source_bead_id": bid}, bead_id_map)
                    for claim in normalized.get("claims") or []
                    if isinstance(claim, dict)
                ]
            )
        if "claim_updates" in normalized:
            normalized["claim_updates"] = _normalize_list(
                [
                    _normalize_claim_update_row(
                        update,
                        bead_id_map=bead_id_map,
                        claim_id_map=claim_id_map,
                    )
                    for update in normalized.get("claim_updates") or []
                    if isinstance(update, dict)
                ]
            )
        beads[bead_key] = normalized

    associations: list[dict[str, Any]] = []
    for row in index.get("associations") or []:
        if not isinstance(row, dict):
            continue
        src = _as_str(row.get("source_bead") or row.get("source_bead_id"))
        tgt = _as_str(row.get("target_bead") or row.get("target_bead_id"))
        rel = _as_str(row.get("relationship")).lower()
        if not src or not tgt or not rel:
            continue
        src_key = bead_id_map.get(src, src)
        tgt_key = bead_id_map.get(tgt, tgt)
        if src not in visible_set and src_key not in visible_set and src_key not in beads:
            continue
        if tgt not in visible_set and tgt_key not in visible_set and tgt_key not in beads:
            continue
        normalized = _strip_keys(row, _VOLATILE_ASSOC_FIELDS)
        normalized["dedupe_key"] = f"assoc:{src_key}:{tgt_key}:{rel}"
        normalized["source_bead_key"] = src_key
        normalized["target_bead_key"] = tgt_key
        normalized.pop("source_bead", None)
        normalized.pop("source_bead_id", None)
        normalized.pop("target_bead", None)
        normalized.pop("target_bead_id", None)
        normalized["relationship"] = rel
        associations.append(normalized)

    entities: dict[str, Any] = {}
    for eid, row in (index.get("entities") or {}).items():
        if not isinstance(row, dict):
            continue
        eid_s = _as_str(eid)
        provenance_rows = [p for p in (row.get("provenance") or []) if isinstance(p, dict)]
        session_provenance_rows = [p for p in provenance_rows if _as_str(p.get("bead_id")) in raw_beads]
        if eid_s not in session_entity_ids and not session_provenance_rows:
            continue
        normalized = _strip_keys(row, _VOLATILE_ENTITY_FIELDS)
        if "aliases" in normalized:
            normalized["aliases"] = _normalize_list(normalized.get("aliases"))
        if "provenance" in normalized:
            prov = []
            for p in session_provenance_rows:
                if isinstance(p, dict):
                    p_norm = _strip_keys(p, {"ts", "created_at", "updated_at"})
                    bead_id = _as_str(p_norm.get("bead_id"))
                    if bead_id:
                        p_norm["bead_key"] = bead_id_map.get(bead_id, bead_id)
                        p_norm.pop("bead_id", None)
                    prov.append(p_norm)
            normalized["provenance"] = _normalize_list(prov)
        entities[_as_str(eid)] = normalized

    return {
        "schema": "session_enrichment_projection.v1",
        "session_id": sid,
        "visible_bead_keys": visible_keys,
        "beads": {k: beads[k] for k in sorted(beads)},
        "associations": _normalize_list(associations),
        "entities": {k: entities[k] for k in sorted(entities)},
    }


def projections_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Return true when two canonical projections have equivalent content."""
    return _canonical_json(left) == _canonical_json(right)


__all__ = [
    "SCHEMA",
    "NORMALIZER_VERSION",
    "DELTA_QUARANTINE_PATH",
    "DELTA_ROW_LIMITS",
    "DELTA_ROW_TYPES",
    "build_window_context_ref",
    "canonical_session_projection",
    "crawler_updates_to_delta",
    "delta_to_crawler_updates",
    "projections_equal",
    "stable_hash",
    "write_delta_quarantine",
]
