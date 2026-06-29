from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EVIDENCE_ATTESTATION_SCHEMA = "causal_continuity.evidence_attestation.v1"

SUPPORTED_SCOPES = {
    "provider_backed_comparison",
    "real_data_leaderboard",
    "t5_llm_judge_primary",
}


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_str(item)
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _load_payload(value: dict[str, Any] | str | Path | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    path = Path(value)
    return json.loads(path.read_text(encoding="utf-8"))


def _entry_rejections(entry: dict[str, Any]) -> list[str]:
    scope = _clean_str(entry.get("scope"))
    reasons: list[str] = []
    if scope not in SUPPORTED_SCOPES:
        reasons.append("unsupported_scope")
    if not bool(entry.get("allow_public_claim")):
        reasons.append("allow_public_claim_required")
    for field in ("reviewer", "evidence_ref", "config_summary"):
        if not _clean_str(entry.get(field)):
            reasons.append(f"missing_{field}")
    if scope == "provider_backed_comparison" and not _clean_list(entry.get("adapter_names")):
        reasons.append("missing_adapter_names")
    if scope == "real_data_leaderboard" and not _clean_list(entry.get("dataset_ids")):
        reasons.append("missing_dataset_ids")
    if scope == "t5_llm_judge_primary":
        if _clean_str(entry.get("judge_kind")) != "llm":
            reasons.append("judge_kind_must_be_llm")
        if not _clean_str(entry.get("prompt_version")):
            reasons.append("missing_prompt_version")
    return reasons


def normalize_evidence_attestation(value: dict[str, Any] | str | Path | None) -> dict[str, Any]:
    """Validate an optional external-evidence attestation payload.

    Attestations never create benchmark evidence. They only document that a
    human reviewer is intentionally allowing a completed configured run to back
    a public claim.
    """

    payload = _load_payload(value)
    if not payload:
        return {
            "schema_version": EVIDENCE_ATTESTATION_SCHEMA,
            "status": "not_provided",
            "valid": False,
            "accepted": [],
            "rejected": [],
            "accepted_scopes": {},
        }
    schema = _clean_str(payload.get("schema_version") or payload.get("schema"))
    entries_raw = payload.get("attestations")
    if entries_raw is None:
        entries_raw = payload.get("entries")
    entries = list(entries_raw or []) if isinstance(entries_raw, list) else []
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if schema != EVIDENCE_ATTESTATION_SCHEMA:
        rejected.append({
            "scope": "",
            "reasons": ["schema_mismatch"],
            "expected_schema": EVIDENCE_ATTESTATION_SCHEMA,
            "actual_schema": schema,
        })
    for raw in entries:
        entry = dict(raw) if isinstance(raw, dict) else {"value": raw}
        scope = _clean_str(entry.get("scope"))
        reasons = _entry_rejections(entry)
        normalized = {
            "scope": scope,
            "reviewer": _clean_str(entry.get("reviewer")),
            "evidence_ref": _clean_str(entry.get("evidence_ref")),
            "config_summary": _clean_str(entry.get("config_summary")),
            "created_at": _clean_str(entry.get("created_at")),
            "adapter_names": _clean_list(entry.get("adapter_names")),
            "dataset_ids": _clean_list(entry.get("dataset_ids")),
            "judge_kind": _clean_str(entry.get("judge_kind")),
            "prompt_version": _clean_str(entry.get("prompt_version")),
            "allow_public_claim": bool(entry.get("allow_public_claim")),
        }
        if schema == EVIDENCE_ATTESTATION_SCHEMA and not reasons:
            accepted.append(normalized)
        else:
            rejected.append({**normalized, "reasons": reasons})
    accepted_scopes: dict[str, list[dict[str, Any]]] = {}
    for row in accepted:
        accepted_scopes.setdefault(str(row.get("scope") or ""), []).append(row)
    return {
        "schema_version": EVIDENCE_ATTESTATION_SCHEMA,
        "status": "accepted" if accepted and not rejected else ("partial" if accepted else "rejected"),
        "valid": bool(accepted) and schema == EVIDENCE_ATTESTATION_SCHEMA,
        "accepted": accepted,
        "rejected": rejected,
        "accepted_scopes": accepted_scopes,
    }


def scope_attested(
    normalized: dict[str, Any],
    scope: str,
    *,
    names: list[str] | tuple[str, ...] | None = None,
) -> bool:
    rows = list((normalized.get("accepted_scopes") or {}).get(scope) or [])
    if not rows:
        return False
    if names is None:
        return True
    wanted = {str(x) for x in names if str(x).strip()}
    if not wanted:
        return False
    key = "dataset_ids" if scope == "real_data_leaderboard" else "adapter_names"
    if scope == "t5_llm_judge_primary":
        return True
    for row in rows:
        if wanted & set(_clean_list(row.get(key))):
            return True
    return False


__all__ = [
    "EVIDENCE_ATTESTATION_SCHEMA",
    "normalize_evidence_attestation",
    "scope_attested",
]
