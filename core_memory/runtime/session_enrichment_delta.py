from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.entity.registry import _is_valid_entity_alias, normalize_entity_alias
from core_memory.schema.normalization import INFERENCE_CANONICAL_RELATION_TYPES

SCHEMA = "session_enrichment_delta.v1"
NORMALIZER_VERSION = "session_enrichment_delta.normalizer.slice_a.1"
CANONICAL_DELTA_RELATIONSHIPS = set(INFERENCE_CANONICAL_RELATION_TYPES)

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


def _normalize_entity_upsert_row(
    row: dict[str, Any],
    *,
    source: str,
    source_bead_key: str | None,
    turn_id: str,
    context_fingerprint: str,
) -> dict[str, Any] | None:
    label = _as_str(row.get("label") or row.get("name") or row.get("value") or row.get("text"))
    normalized_label = normalize_entity_alias(label)
    if not _is_valid_entity_alias(label, normalized_label):
        return None
    aliases = _str_list(row.get("aliases") or [label], limit=12)
    alias_norms = sorted({normalize_entity_alias(alias) for alias in aliases if normalize_entity_alias(alias)})
    if normalized_label not in alias_norms:
        alias_norms.insert(0, normalized_label)

    out = {
        "label": label,
        "normalized_label": normalized_label,
        "aliases": alias_norms[:12],
        "entity_kind": _as_str(row.get("entity_kind") or row.get("kind")) or "other",
        "source_bead_key": source_bead_key,
        "evidence": _as_str(row.get("evidence")) or None,
    }
    out.update(
        _base_row(
            dedupe_key=f"entity:{normalized_label}",
            confidence=row.get("confidence", 0.72),
            provenance_kind=_as_str(row.get("provenance")) or "model_inferred",
            source=source,
            turn_id=turn_id,
            evidence_refs=row.get("evidence_refs") or [],
            context_fingerprint=context_fingerprint,
            rationale=_as_str(row.get("evidence") or row.get("rationale")) or None,
        )
    )
    return out


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
                provenance_kind=_as_str(row.get("provenance")) or "model_inferred",
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
        for entity_label in _str_list(row.get("entities"), limit=40):
            entity_candidates.append(
                (
                    {"label": entity_label, "aliases": [entity_label], "confidence": row.get("confidence", 0.72)},
                    bead_dedupe_key,
                    "crawler_updates.beads_create.entities",
                )
            )

    explicit_entity_rows = [r for r in raw.get("entity_upserts") or [] if isinstance(r, dict)]
    for row in explicit_entity_rows:
        entity_candidates.append(
            (row, _as_str(row.get("source_bead_key")) or None, "crawler_updates.entity_upserts")
        )

    entity_candidates, q = _bounded(entity_candidates, "entity_upserts", session_id=sid, turn_id=tid)
    quarantine_rows.extend(q)
    seen_entity_keys: set[str] = set()
    for row, source_bead_key, source in entity_candidates:
        normalized_entity = _normalize_entity_upsert_row(
            row,
            source=source,
            source_bead_key=source_bead_key,
            turn_id=tid,
            context_fingerprint=ctx_fp,
        )
        if not normalized_entity:
            quarantine_rows.append(
                _quarantine("entity_upserts", row, ["invalid_entity_label"], session_id=sid, turn_id=tid)
            )
            continue
        key = str(normalized_entity.get("dedupe_key") or "")
        if key in seen_entity_keys:
            continue
        seen_entity_keys.add(key)
        delta["entity_upserts"].append(normalized_entity)

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
        if rel not in CANONICAL_DELTA_RELATIONSHIPS:
            quarantined = dict(row)
            quarantined["relationship_raw"] = rel
            quarantine_rows.append(
                _quarantine(
                    "associations",
                    quarantined,
                    [f"noncanonical_relationship:{rel or 'empty'}"],
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
        normalized = _strip_keys(row, _VOLATILE_ENTITY_FIELDS)
        if "aliases" in normalized:
            normalized["aliases"] = _normalize_list(normalized.get("aliases"))
        if "provenance" in normalized:
            prov = []
            for p in normalized.get("provenance") or []:
                if isinstance(p, dict):
                    prov.append(_strip_keys(p, {"ts", "created_at", "updated_at"}))
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
    "build_window_context_ref",
    "canonical_session_projection",
    "crawler_updates_to_delta",
    "delta_to_crawler_updates",
    "projections_equal",
    "stable_hash",
]
