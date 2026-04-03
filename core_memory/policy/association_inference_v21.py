from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CANONICAL_INFERENCE_RELATIONSHIPS = {
    "caused_by",
    "supports",
    "supersedes",
    "blocked_by",
    "unblocks",
    "enables",
    "derived_from",
    "follows",
    "contradicts",
}

NONCANONICAL_TEMPORAL_RELATIONSHIPS = {"precedes"}

INFERENCE_MODE_STRICT = "strict"
INFERENCE_MODE_PERMISSIVE = "permissive"

WARN_NONCANONICAL_PREFIX = "noncanonical_relationship:"
WARN_NORMALIZED_UNKNOWN = "unknown_relationship_normalized"
WARN_ALIAS_RATIONALE_TO_REASON_TEXT = "field_alias_applied:rationale->reason_text"

Q_MISSING_SOURCE_OR_TARGET = "missing_source_or_target"
Q_SELF_LINK = "self_link"
Q_MISSING_REASON_TEXT = "missing_reason_text"
Q_MISSING_OR_INVALID_CONFIDENCE = "missing_or_invalid_confidence"
Q_NONCANONICAL_PREFIX = "noncanonical_relationship:"


@dataclass
class ValidationResult:
    ok: bool
    record: dict[str, Any]
    warnings: list[str]
    quarantine_reasons: list[str]


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _normalize_mode(mode: str) -> str:
    m = str(mode or INFERENCE_MODE_STRICT).strip().lower()
    if m not in {INFERENCE_MODE_STRICT, INFERENCE_MODE_PERMISSIVE}:
        return INFERENCE_MODE_STRICT
    return m


def _coerce_reason_text(payload: dict[str, Any], warnings: list[str]) -> str:
    reason_text = str(payload.get("reason_text") or "").strip()
    if reason_text:
        return reason_text

    rationale = str(payload.get("rationale") or "").strip()
    if rationale:
        _append_unique(warnings, WARN_ALIAS_RATIONALE_TO_REASON_TEXT)
        return rationale
    return ""


def _coerce_confidence(payload: dict[str, Any]) -> float | None:
    raw = payload.get("confidence")
    if raw is None:
        return None
    try:
        conf = float(raw)
    except (TypeError, ValueError):
        return None
    if 0.0 <= conf <= 1.0:
        return conf
    return None


def _noncanonical_reason(rel: str) -> str:
    return f"{Q_NONCANONICAL_PREFIX}{rel or 'empty'}"


def validate_and_normalize_inference_payload(payload: dict[str, Any], *, mode: str = INFERENCE_MODE_STRICT) -> ValidationResult:
    """Validate and normalize model-inferred association payloads (v2.1 contract).

    This path is inference-specific and intentionally does not replace legacy
    association normalization/validation behavior.
    """
    mode_n = _normalize_mode(mode)
    warnings: list[str] = []
    quarantine_reasons: list[str] = []

    source_bead = str(payload.get("source_bead") or payload.get("source_bead_id") or "").strip()
    target_bead = str(payload.get("target_bead") or payload.get("target_bead_id") or "").strip()
    relationship_raw = str(payload.get("relationship") or "").strip().lower()
    provenance = str(payload.get("provenance") or "model_inferred").strip().lower() or "model_inferred"
    reason_text = _coerce_reason_text(payload, warnings)
    confidence = _coerce_confidence(payload)

    if not source_bead or not target_bead:
        _append_unique(quarantine_reasons, Q_MISSING_SOURCE_OR_TARGET)
    if source_bead and source_bead == target_bead:
        _append_unique(quarantine_reasons, Q_SELF_LINK)

    if provenance == "model_inferred":
        if not reason_text:
            _append_unique(quarantine_reasons, Q_MISSING_REASON_TEXT)
        if confidence is None:
            _append_unique(quarantine_reasons, Q_MISSING_OR_INVALID_CONFIDENCE)

    normalized_relationship = relationship_raw
    normalization_applied = False

    if relationship_raw not in CANONICAL_INFERENCE_RELATIONSHIPS:
        reason = _noncanonical_reason(relationship_raw)
        _append_unique(warnings, f"{WARN_NONCANONICAL_PREFIX}{relationship_raw or 'empty'}")
        if mode_n == INFERENCE_MODE_STRICT:
            _append_unique(quarantine_reasons, reason)
        else:
            normalized_relationship = "associated_with"
            normalization_applied = True
            _append_unique(warnings, WARN_NORMALIZED_UNKNOWN)

    record = {
        "source_bead": source_bead,
        "target_bead": target_bead,
        "relationship": normalized_relationship,
        "relationship_raw": relationship_raw,
        "reason_text": reason_text,
        "confidence": confidence,
        "provenance": provenance,
        "reason_code": payload.get("reason_code"),
        "evidence_fields": list(payload.get("evidence_fields") or []),
        "normalization_applied": normalization_applied,
        "warnings": list(warnings),
    }

    return ValidationResult(
        ok=len(quarantine_reasons) == 0,
        record=record,
        warnings=list(warnings),
        quarantine_reasons=list(quarantine_reasons),
    )
