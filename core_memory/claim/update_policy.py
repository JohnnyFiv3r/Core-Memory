"""
Per-turn claim update emission.

Governance intent:
- Claims are authored during canonical turn finalization.
- ClaimUpdates are emitted during the session-window decision phase,
  not during extraction.
"""
from __future__ import annotations

import uuid
from typing import Any

from core_memory.persistence.store_claim_ops import compute_claim_grounding_hash, resolve_current_state, write_claim_updates_to_bead


_INVALIDATING_DECISIONS = {"supersede", "retract", "conflict"}


def _as_text(x: Any) -> str:
    return str(x or "").strip()


def _decision(x: Any) -> str:
    return str(x or "").strip().lower()


def _coerce_confidence(x: Any, default: float = 0.8) -> float:
    try:
        f = float(x)
    except Exception:
        f = float(default)
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _with_grounding(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    evidence = [str(x).strip() for x in (out.get("evidence_bead_ids") or []) if str(x).strip()]
    trig = _as_text(out.get("trigger_bead_id"))
    if trig and trig not in evidence:
        evidence.append(trig)
    out["evidence_bead_ids"] = sorted(set(evidence))
    out["judge_model"] = _as_text(out.get("judge_model")) or "current-runtime"
    out["prompt_version"] = _as_text(out.get("prompt_version")) or "current-runtime"
    out["rubric_version"] = _as_text(out.get("rubric_version")) or "current-runtime"
    if not _as_text(out.get("grounding_hash")):
        out["grounding_hash"] = compute_claim_grounding_hash(out)
    return out


def _update_dedupe_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    target = _as_text(row.get("target_claim_id"))
    grounding_hash = _as_text(row.get("grounding_hash"))
    if target and grounding_hash:
        return ("grounding", target, grounding_hash, "")
    return (
        _decision(row.get("decision")),
        target,
        _as_text(row.get("replacement_claim_id")),
        _as_text(row.get("trigger_bead_id")),
    )


def _normalize_explicit_updates(
    rows: list[dict] | None,
    *,
    trigger_bead_id: str,
) -> list[dict]:
    out: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        d = _decision(row.get("decision"))
        if d not in {"reaffirm", "supersede", "retract", "conflict"}:
            continue

        target = _as_text(row.get("target_claim_id") or row.get("claim_id"))
        replacement = _as_text(row.get("replacement_claim_id") or row.get("successor_claim_id")) or None
        subject = _as_text(row.get("subject"))
        slot = _as_text(row.get("slot"))
        reason = _as_text(row.get("reason_text")) or "session decision pass"
        trig = _as_text(row.get("trigger_bead_id")) or trigger_bead_id

        if not target:
            continue
        if d in _INVALIDATING_DECISIONS and not trig:
            continue

        out.append(
            _with_grounding(
                {
                    "id": _as_text(row.get("id")) or str(uuid.uuid4()),
                    "decision": d,
                    "target_claim_id": target,
                    "replacement_claim_id": replacement,
                    "subject": subject,
                    "slot": slot,
                    "reason_text": reason,
                    "trigger_bead_id": trig or None,
                    "confidence": _coerce_confidence(row.get("confidence"), default=0.8),
                    "evidence_bead_ids": list(row.get("evidence_bead_ids") or []),
                    "judge_model": _as_text(row.get("judge_model")) or "current-runtime",
                    "prompt_version": _as_text(row.get("prompt_version")) or "current-runtime",
                    "rubric_version": _as_text(row.get("rubric_version")) or "current-runtime",
                    "grounding_hash": _as_text(row.get("grounding_hash")),
                }
            )
        )
    return out


def emit_claim_updates(
    root: str,
    new_claims: list[dict],
    trigger_bead_id: str,
    *,
    session_id: str | None = None,
    visible_bead_ids: list[str] | None = None,
    reviewed_updates: dict[str, Any] | None = None,
    decision_pass: dict[str, Any] | None = None,
) -> list[dict]:
    """
    Emit ClaimUpdates in decision phase.

    Sources:
    1) Explicit decision-pass/crawler claim_updates rows, when provided.
    2) Session-window state reconciliation against newly authored claims:
       - same value -> reaffirm
       - changed value -> supersede
    """
    trig = _as_text(trigger_bead_id)
    if not trig:
        return []

    emitted: list[dict] = []
    dedupe: set[tuple[str, str, str, str]] = set()

    explicit_rows: list[dict] = []
    if isinstance(reviewed_updates, dict):
        explicit_rows.extend(list(reviewed_updates.get("claim_updates") or []))
    if isinstance(decision_pass, dict):
        explicit_rows.extend(list(decision_pass.get("claim_updates") or []))

    for update in _normalize_explicit_updates(explicit_rows, trigger_bead_id=trig):
        key = _update_dedupe_key(update)
        if key in dedupe:
            continue
        dedupe.add(key)
        emitted.append(update)

    # Auto reconciliation over session-window current state for new claims.
    for claim in new_claims or []:
        if not isinstance(claim, dict):
            continue
        subject = _as_text(claim.get("subject"))
        slot = _as_text(claim.get("slot"))
        new_id = _as_text(claim.get("id"))
        if not subject or not slot or not new_id:
            continue

        state = resolve_current_state(root, subject, slot)
        existing = state.get("current_claim") if isinstance(state, dict) else None
        if not isinstance(existing, dict):
            continue
        old_id = _as_text(existing.get("id"))
        if not old_id or old_id == new_id:
            continue

        old_value = existing.get("value")
        new_value = claim.get("value")

        if old_value == new_value:
            update = {
                "id": str(uuid.uuid4()),
                "decision": "reaffirm",
                "target_claim_id": old_id,
                "replacement_claim_id": None,
                "subject": subject,
                "slot": slot,
                "reason_text": "session-window decision pass: reaffirm existing claim",
                "trigger_bead_id": trig,
                "confidence": _coerce_confidence(claim.get("confidence"), default=0.8),
            }
        else:
            update = {
                "id": str(uuid.uuid4()),
                "decision": "supersede",
                "target_claim_id": old_id,
                "replacement_claim_id": new_id,
                "subject": subject,
                "slot": slot,
                "reason_text": "session-window decision pass: new claim supersedes existing",
                "trigger_bead_id": trig,
                "confidence": _coerce_confidence(claim.get("confidence"), default=0.8),
            }

        evidence_bead_ids = [trig]
        old_source = _as_text(existing.get("source_bead_id"))
        if old_source:
            evidence_bead_ids.append(old_source)
        update["evidence_bead_ids"] = evidence_bead_ids
        update = _with_grounding(update)
        key = _update_dedupe_key(update)
        if key in dedupe:
            continue
        dedupe.add(key)
        emitted.append(update)

    if emitted:
        write_claim_updates_to_bead(root, trig, emitted)

    return emitted
