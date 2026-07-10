from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.association.quarantine import write_quarantine
from core_memory.persistence.bead_hygiene_contract import retrieval_eligibility_downgrade_reasons
from core_memory.persistence.io_utils import append_jsonl, store_lock
from core_memory.persistence.session_surface import read_session_surface
from core_memory.persistence.store import MemoryStore
from core_memory.policy.association_contract import assoc_dedupe_key
from core_memory.policy.association_inference_v21 import (
    INFERENCE_MODE_PERMISSIVE,
    INFERENCE_MODE_STRICT,
    validate_and_normalize_inference_payload,
)
from core_memory.schema.agent_authored_updates import (
    AGENT_AUTHORED_UPDATES_V1,
    AGENT_AUTHORED_V1_BEAD_FIELDS,
    AGENT_OWNED_BEAD_FIELDS,
    CREATION_STRUCTURAL_INPUT_FIELDS,
    NORMALIZABLE_CREATION_ROW_FIELDS,
    agent_authored_updates_json_schema,
)
from core_memory.schema.agent_authoring_spec import BEAD_AUTHORING_SPEC
from core_memory.schema.event_schemas import CRAWLER_UPDATE
from core_memory.schema.normalization import normalize_state_change


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
        for a in row.get("associations") or []:
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
                        "evidence_bead_ids": a.get("evidence_bead_ids"),
                        "evidence_refs": a.get("evidence_refs"),
                        "judge_model": a.get("judge_model"),
                        "prompt_version": a.get("prompt_version"),
                        "rubric_version": a.get("rubric_version"),
                        "grounding_hash": a.get("grounding_hash"),
                        "turn_id": a.get("turn_id"),
                        "visible_bead_ids": a.get("visible_bead_ids"),
                        "relationship_raw": a.get("relationship_raw"),
                    }
                )

        for act in row.get("association_actions") or []:
            if isinstance(act, dict):
                lifecycle_rows.append(
                    {
                        "association_id": str(act.get("association_id") or "").strip(),
                        "action": str(act.get("action") or "").strip().lower(),
                        "replacement_association_id": str(act.get("replacement_association_id") or "").strip(),
                        "reason_text": str(act.get("reason_text") or act.get("reason") or "").strip(),
                        "confidence": act.get("confidence"),
                        "provenance": str(act.get("provenance") or "model_inferred").strip().lower()
                        or "model_inferred",
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
                "evidence_bead_ids": list(a.get("evidence_bead_ids") or []),
                "evidence_refs": list(a.get("evidence_refs") or []),
                "judge_model": a.get("judge_model"),
                "prompt_version": a.get("prompt_version"),
                "rubric_version": a.get("rubric_version"),
                "grounding_hash": a.get("grounding_hash"),
                "turn_id": str(a.get("turn_id") or "").strip(),
                "visible_bead_ids": [str(x).strip() for x in (a.get("visible_bead_ids") or []) if str(x).strip()],
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


CURRENT_TURN_ASSOC_SOURCE_ALIASES = {
    "__current_turn__",
    "current_turn",
    "$current_turn",
    "@current_turn",
}


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default


def _normalize_creation_rows_with_diagnostics(
    updates: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Copy schema-known fields and return every compatibility loss explicitly."""

    raw_rows = list((updates or {}).get("beads_create") or [])
    full_retrieval_contract = str((updates or {}).get("schema_version") or "") == AGENT_AUTHORED_UPDATES_V1
    candidates: list[tuple[int, dict[str, Any]]] = []
    diagnostics: list[dict[str, Any]] = []

    for row_index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            diagnostics.append({"row_index": row_index, "code": "creation_row_not_object"})
            continue

        unknown = sorted(str(key) for key in raw if key not in NORMALIZABLE_CREATION_ROW_FIELDS)
        if unknown:
            diagnostics.append(
                {
                    "row_index": row_index,
                    "code": "unknown_fields_dropped",
                    "dropped_fields": unknown,
                }
            )

        row = {key: deepcopy(value) for key, value in raw.items() if key in NORMALIZABLE_CREATION_ROW_FIELDS}
        row["type"] = str(row.get("type") or "context").strip() or "context"
        row["title"] = (str(row.get("title") or "").strip() or "assistant turn")[:200]

        summary = row.get("summary")
        if isinstance(summary, str):
            summary = [summary]
        if not isinstance(summary, list):
            summary = []
        row["summary"] = [str(item).strip() for item in summary if str(item).strip()][:5]

        source_turn_ids = row.get("source_turn_ids")
        if isinstance(source_turn_ids, str):
            source_turn_ids = [source_turn_ids]
        row["source_turn_ids"] = [str(item) for item in (source_turn_ids or []) if str(item).strip()][:5]
        retrieval_eligibility_missing = "retrieval_eligible" not in row
        row["retrieval_eligible"] = _as_bool(row.get("retrieval_eligible"), default=False)
        if "state_change" in row:
            row["state_change"] = normalize_state_change(row.get("state_change"))

        warnings = [f"unknown_authored_field:dropped:{field_name}" for field_name in unknown]
        if retrieval_eligibility_missing:
            warnings.append("retrieval_eligible:missing_defaulted_false")
            diagnostics.append(
                {
                    "row_index": row_index,
                    "code": "compatibility_default_applied",
                    "field": "retrieval_eligible",
                    "value": False,
                }
            )
        eligibility_reasons = (
            retrieval_eligibility_downgrade_reasons(row, full_contract=full_retrieval_contract)
            if row["retrieval_eligible"]
            else []
        )
        if eligibility_reasons:
            row["retrieval_eligible"] = False
            if full_retrieval_contract:
                warnings.extend(f"retrieval_eligible:downgraded:{reason}" for reason in eligibility_reasons)
                diagnostics.append(
                    {
                        "row_index": row_index,
                        "code": "retrieval_eligibility_downgraded",
                        "reasons": eligibility_reasons,
                        "quality_contract": AGENT_AUTHORED_UPDATES_V1,
                    }
                )
            else:
                warnings.append("retrieval_eligible:downgraded_generic_title")
                diagnostics.append(
                    {
                        "row_index": row_index,
                        "code": "retrieval_eligibility_downgraded",
                        "reason": "generic_title",
                    }
                )
        if warnings:
            row["validation_warnings"] = warnings
        candidates.append((row_index, row))

    explicit_primary = [
        index
        for index, (_, row) in enumerate(candidates)
        if str(row.get("creation_role") or "").strip().lower() == "current_turn"
    ]
    primary_index = explicit_primary[0] if explicit_primary else (0 if candidates else -1)

    primary: list[tuple[int, dict[str, Any]]] = []
    derived: list[tuple[int, dict[str, Any]]] = []
    for candidate_index, (row_index, row) in enumerate(candidates):
        role = str(row.get("creation_role") or "").strip().lower()
        if candidate_index == primary_index:
            row["creation_role"] = "current_turn"
            if role != "current_turn":
                row.setdefault("validation_warnings", []).append("creation_role:missing_defaulted_to_current_turn")
            primary.append((row_index, row))
            continue

        if role == "current_turn":
            diagnostics.append(
                {
                    "row_index": row_index,
                    "code": "duplicate_current_turn_row_dropped",
                }
            )
            continue

        row["creation_role"] = "derived"
        if role != "derived":
            row.setdefault("validation_warnings", []).append("creation_role:missing_defaulted_to_derived")
        derived_from = [str(item) for item in (row.get("derived_from_bead_ids") or []) if str(item).strip()]
        if "$current_turn" not in derived_from:
            derived_from.append("$current_turn")
            row.setdefault("validation_warnings", []).append("derived_from_bead_ids:current_turn_sentinel_added")
        row["derived_from_bead_ids"] = derived_from
        derived.append((row_index, row))

    if len(derived) > 2:
        for row_index, _ in derived[2:]:
            diagnostics.append(
                {
                    "row_index": row_index,
                    "code": "derived_row_limit_exceeded_dropped",
                    "max_derived_rows": 2,
                }
            )
        derived = derived[:2]

    ordered = primary + derived
    if ordered and not ordered[0][1].get("source_turn_ids"):
        diagnostics.append(
            {
                "row_index": ordered[0][0],
                "code": "current_turn_row_missing_source_turn_ids",
            }
        )
        return [], diagnostics
    return [row for _, row in ordered], diagnostics


def _normalize_creation_rows(updates: dict[str, Any]) -> list[dict[str, Any]]:
    rows, _ = _normalize_creation_rows_with_diagnostics(updates)
    return rows


def _creation_store_payload(row: dict[str, Any], *, session_id: str) -> dict[str, Any]:
    """Project one normalized row onto Bead fields plus runtime overlay."""

    payload = {key: deepcopy(value) for key, value in row.items() if key in AGENT_OWNED_BEAD_FIELDS}
    for key in CREATION_STRUCTURAL_INPUT_FIELDS | {"validity"}:
        if key in row:
            payload[key] = deepcopy(row[key])
    payload["session_id"] = str(session_id)
    payload["validation_warnings"] = list(row.get("validation_warnings") or [])
    payload.setdefault("type", "context")
    payload.setdefault("title", "assistant turn")
    payload.setdefault("summary", [])
    payload.setdefault("retrieval_eligible", False)
    return payload


def build_crawler_context(
    root: str,
    session_id: str,
    limit: int = 200,
    carry_in_bead_ids: list[str] | None = None,
) -> dict[str, Any]:
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
        "authoring_spec": BEAD_AUTHORING_SPEC,
        "agent_authored_updates_schema": agent_authored_updates_json_schema(),
        "writing_contract": [
            "Every turn has exactly one canonical creation_role=current_turn bead.",
            (
                "A turn may also author at most two creation_role=derived "
                "companion beads linked with derived_from_bead_ids=['$current_turn']."
            ),
            "Thin beads preserve temporal continuity; rich beads carry structured retrieval payload.",
            "Summary is optional; do not invent prose when structured fields are stronger.",
            "Initial write requires temporal grounding only (session/turn order/prev bead).",
            (
                "Every non-initial turn should preserve temporal baseline continuity with at least "
                "one temporal association such as follows/precedes to the immediately adjacent "
                "session bead when that adjacency is visible."
            ),
            "Do not force broad causal or semantic links on initial write unless strongly grounded.",
            (
                "After the first turn in a session, actively sweep plausible prior visible beads "
                "against the defined association types and append every non-temporal semantic "
                "relation that is strongly or highly plausibly supported."
            ),
            (
                "When multiple relationships are highly plausibly true, append all of them "
                "rather than choosing only one."
            ),
            (
                "High plausibility is enough for append-only associations; certainty is not "
                "required, but do not invent links unsupported by the visible record."
            ),
            (
                "Use canonical relationship types only. Put free-text justification in reason_text "
                "or rationale, and express uncertainty with confidence rather than inventing new "
                "relation labels."
            ),
            (
                "Prefer specific semantic links like supports, refines, caused_by, enables, "
                "diagnoses, resolves, supersedes, or contradicts over generic or purely temporal "
                "links when the turn meaningfully updates prior memory."
            ),
            (
                "If no non-temporal semantic link is strongly or highly plausibly supported, "
                "omit it rather than fabricating one."
            ),
        ],
        "retrieval_contract": [
            "retrieval_eligible is authored; a missing compatibility value defaults to false.",
            (
                "For agent_authored_updates.v1, true requires a non-generic title, useful retrieval_title, "
                "at least one retrieval_fact, and at least one grounded quality signal."
            ),
            "Legacy unversioned compatibility writes retain the prior generic-title-only downgrade.",
        ],
        "allowed_updates": {
            "beads_create": {
                "shape": "list[AgentAuthoredBeadCreation]",
                "fields": sorted(AGENT_AUTHORED_V1_BEAD_FIELDS),
                "primary_role": "exactly one current_turn",
                "derived_role": "zero to two derived rows",
            },
            "reviewed_beads": "list[{bead_id,promotion_state,reason?,associations?}]",
            "associations": (
                "list[{source_bead_id,target_bead_id,relationship,reason_text,confidence,"
                "provenance?,reason_code?,evidence_fields?,relationship_raw?,rationale?}]"
            ),
        },
        "append_only_rules": [
            "promotion_marked can only be set true and means preserve_full_in_rolling semantics",
            "associations are append-only records",
            "source must be session-local bead",
            "target must be in visible_bead_ids set",
            "initial-write minimum association is temporal only; richer associations may be appended later",
            (
                "for non-initial turns, temporal continuity is baseline: preserve at least one "
                "temporal adjacency link when the adjacent bead is visible"
            ),
            (
                "for non-initial turns, review the candidate relationship list against each "
                "plausible target and append every high-plausibility semantic relation that applies"
            ),
            (
                "do not collapse distinct plausible relations into one weak default if multiple "
                "stronger relations are justified by the visible evidence"
            ),
            "uncertainty belongs in confidence and explanation fields, not in free-text relationship labels",
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

                if (
                    rel == "precedes"
                    and str(row.get("provenance") or "model_inferred").strip().lower() == "model_inferred"
                ):
                    write_quarantine(
                        Path(root),
                        row,
                        reasons=["noncanonical_relationship:precedes"],
                        warnings=["noncanonical_relationship:precedes"],
                        original_payload=row,
                        session_id=str(session_id),
                    )
                    quarantined += 1
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
                        "evidence_bead_ids": list(row.get("evidence_bead_ids") or []),
                        "evidence_refs": list(row.get("evidence_refs") or []),
                        "judge_model": row.get("judge_model"),
                        "prompt_version": row.get("prompt_version"),
                        "rubric_version": row.get("rubric_version"),
                        "grounding_hash": row.get("grounding_hash"),
                        "turn_id": row.get("turn_id"),
                        "visible_bead_ids": list(row.get("visible_bead_ids") or []),
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
                        "evidence_bead_ids": list(row_n.get("evidence_bead_ids") or []),
                        "evidence_refs": list(row_n.get("evidence_refs") or []),
                        "judge_model": row_n.get("judge_model"),
                        "prompt_version": row_n.get("prompt_version"),
                        "rubric_version": row_n.get("rubric_version"),
                        "grounding_hash": row_n.get("grounding_hash"),
                        "turn_id": row_n.get("turn_id") or None,
                        "visible_bead_ids": list(row_n.get("visible_bead_ids") or []),
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


_TEMPORAL_RELATIONS = {"follows", "precedes"}


def _maybe_upgrade_context_to_reflection(
    *,
    root: Any,
    session_id: str,
    associations: list[dict],
    store: Any = None,
) -> None:
    """Upgrade context beads to reflection when ≥1 backward non-temporal causal edge lands."""
    if not associations:
        return
    from pathlib import Path

    from core_memory.persistence.io_utils import atomic_write_json

    root_path = Path(root)
    index_file = root_path / ".beads" / "index.json"
    if not index_file.exists():
        return

    candidates: dict[str, int] = {}  # bead_id -> causal edge count
    for assoc in associations:
        rel = str(assoc.get("relationship") or "").strip().lower()
        if rel in _TEMPORAL_RELATIONS:
            continue
        target = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "").strip()
        if target:
            candidates[target] = candidates.get(target, 0) + 1

    if not candidates:
        return

    with store_lock(root_path):
        import json as _json
        from datetime import datetime
        from datetime import timezone as _tz

        try:
            index = _json.loads(index_file.read_text(encoding="utf-8"))
        except Exception:
            return
        beads = index.get("beads") or {}
        changed = False
        now_iso = datetime.now(_tz.utc).isoformat()
        for bead_id, edge_count in candidates.items():
            bead = beads.get(bead_id)
            if not isinstance(bead, dict):
                continue
            if str(bead.get("type") or "") != "context":
                continue
            # Upgrade to reflection
            bead["type"] = "reflection"
            if not bead.get("reflection_type"):
                bead["reflection_type"] = "meta_analysis"
            tlog = list(bead.get("type_log") or [])
            tlog.append(
                {
                    "type": "reflection",
                    "set_at": now_iso,
                    "reason": "causal_crawler",
                    "edge_count": edge_count,
                }
            )
            bead["type_log"] = tlog
            beads[bead_id] = bead
            changed = True
        if changed:
            index["beads"] = beads
            atomic_write_json(index_file, index)


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
    created_bead_ids: list[str] = []
    derived_bead_ids: list[str] = []
    derived_failures: list[dict[str, Any]] = []
    creation_rows, creation_diagnostics = _normalize_creation_rows_with_diagnostics(updates or {})
    current_turn_bead_id = ""
    if creation_rows:
        store = MemoryStore(root)
        for row_index, row in enumerate(creation_rows):
            role = str(row.get("creation_role") or "").strip().lower()
            row_for_store = dict(row)
            if role == "derived":
                if not current_turn_bead_id:
                    derived_failures.append(
                        {
                            "row_index": row_index,
                            "code": "current_turn_bead_not_committed",
                        }
                    )
                    continue
                row_for_store["derived_from_bead_ids"] = [
                    current_turn_bead_id if str(item) == "$current_turn" else str(item)
                    for item in (row.get("derived_from_bead_ids") or [])
                    if str(item).strip()
                ]

            try:
                bead_id = store.add_bead(**_creation_store_payload(row_for_store, session_id=str(session_id)))
            except Exception as exc:
                if role != "derived":
                    raise
                derived_failures.append(
                    {
                        "row_index": row_index,
                        "code": "derived_bead_persistence_failed",
                        "error": str(exc),
                    }
                )
                continue
            created += 1
            if bead_id:
                created_bead_ids.append(str(bead_id))
            if role == "current_turn":
                current_turn_bead_id = str(bead_id or "")
            elif bead_id:
                derived_bead_ids.append(str(bead_id))

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
        if current_turn_bead_id:
            for assoc_row in assoc_rows:
                src = str(assoc_row.get("source_bead") or "").strip()
                if src.lower() in CURRENT_TURN_ASSOC_SOURCE_ALIASES:
                    assoc_row["source_bead"] = current_turn_bead_id
        now = datetime.now(timezone.utc).isoformat()
        log_path = _crawler_updates_log_path(root, session_id)
        for diagnostic in creation_diagnostics:
            append_jsonl(
                log_path,
                {
                    "schema": CRAWLER_UPDATE,
                    "kind": "creation_validation",
                    "session_id": str(session_id),
                    **diagnostic,
                    "created_at": now,
                },
            )
        strict_default = os.environ.get("CORE_MEMORY_ASSOC_STRICT", "1") != "0"
        inference_mode = INFERENCE_MODE_STRICT if strict_default else INFERENCE_MODE_PERMISSIVE
        quarantine_path = Path(root) / ".beads" / "events" / "association-quarantine.jsonl"

        existing_assoc_keys: set[tuple[str, str, str]] = set()
        existing_assoc_by_id: dict[str, dict[str, Any]] = {}
        for a in index.get("associations") or []:
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
                    "schema": CRAWLER_UPDATE,
                    "kind": "promotion_mark",
                    "session_id": str(session_id),
                    "bead_id": str(bid),
                    "promotion_scope": "rolling_continuity",
                    "created_at": now,
                },
            )
            promoted += 1

        appended = 0
        accepted_assocs: list[dict] = []
        lifecycle_queued = 0
        lifecycle_rejected = 0
        for row in assoc_rows:
            if not isinstance(row, dict):
                continue
            # Fill missing relationship using preview classifier as fallback.
            # Never overrides an agent-supplied value; only fires when the field is absent.
            # Note: _normalize_review_rows defaults provenance to "model_inferred", so we
            # must set provenance unconditionally here when the classifier provides the relationship.
            if not str(row.get("relationship") or "").strip():
                src0 = str(row.get("source_bead") or "")
                tgt0 = str(row.get("target_bead") or "")
                sb0 = beads.get(src0)
                tb0 = beads.get(tgt0)
                if sb0 and tb0:
                    from core_memory.association.preview import infer_relationship

                    row = dict(row)
                    row["relationship"], _rc = infer_relationship(sb0, tb0)
                    if not str(row.get("reason_code") or "").strip():
                        row["reason_code"] = _rc
                    row["provenance"] = "preview_classifier"
            if (
                str(row.get("relationship") or "").strip().lower() == "precedes"
                and str(row.get("provenance") or "model_inferred").strip().lower() == "model_inferred"
            ):
                write_quarantine(
                    Path(root),
                    row,
                    reasons=["noncanonical_relationship:precedes"],
                    warnings=["noncanonical_relationship:precedes"],
                    original_payload=row,
                    session_id=str(session_id),
                )
                quarantined += 1
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
                    "schema": CRAWLER_UPDATE,
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
                    "evidence_bead_ids": list(row_n.get("evidence_bead_ids") or []),
                    "evidence_refs": list(row_n.get("evidence_refs") or []),
                    "judge_model": row_n.get("judge_model"),
                    "prompt_version": row_n.get("prompt_version"),
                    "rubric_version": row_n.get("rubric_version"),
                    "grounding_hash": row_n.get("grounding_hash"),
                    "turn_id": row_n.get("turn_id") or None,
                    "visible_bead_ids": list(row_n.get("visible_bead_ids") or []),
                    "normalization_applied": bool(row_n.get("normalization_applied", False)),
                    "created_at": now,
                },
            )
            queued_assoc_keys.add(dedupe_key)
            appended += 1
            accepted_assocs.append({"target_bead": tgt, "source_bead": src, "relationship": rel_n})

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
                if str(rb_s.get("session_id") or "") != str(session_id) or str(rb_t.get("session_id") or "") != str(
                    session_id
                ):
                    lifecycle_rejected += 1
                    continue

            append_jsonl(
                log_path,
                {
                    "schema": CRAWLER_UPDATE,
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

    # Type upgrade intentionally removed: re-typing every bead with a causal edge
    # to reflection/meta_analysis produced implausible classification (91/92 beads
    # in LoCoMo conv-26 were re-typed). Beads keep their authored/inferred type;
    # the judge assigns the correct type in judge mode.

    return {
        "ok": True,
        "beads_created": created,
        "created_bead_ids": created_bead_ids,
        "current_turn_bead_id": current_turn_bead_id,
        "derived_bead_ids": derived_bead_ids,
        "derived_failures": derived_failures,
        "creation_diagnostics": creation_diagnostics,
        "creation_dropped_fields": sorted(
            {
                str(field_name)
                for diagnostic in creation_diagnostics
                for field_name in (diagnostic.get("dropped_fields") or [])
            }
        ),
        "promotions_marked": promoted,
        "associations_appended": appended,
        "association_lifecycle_queued": lifecycle_queued,
        "association_lifecycle_rejected": lifecycle_rejected,
        "associations_quarantined": quarantined,
        "quarantine_path": str(quarantine_path),
        "queued_to": str(log_path),
        "authority_path": "session_side_log",
    }
