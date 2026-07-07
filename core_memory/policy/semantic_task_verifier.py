from __future__ import annotations

"""Verifier helper for semantic task outputs.

The verifier is deliberately non-authoritative: it can block, warn, or route
outputs to review, but it cannot approve truth or apply canonical writes.
"""

import json
from typing import Any

from core_memory.policy.semantic_task_runtime import get_semantic_task_runtime
from core_memory.schema.semantic_tasks import SemanticTaskRequest, SemanticTaskRuntime
from core_memory.schema.semantic_tasks import TASK_VERIFIER

VERIFIER_CONTRACT = "memory.semantic_task_verifier.v1"
VERIFIER_PROMPT_VERSION = "semantic_task_verifier.v1"
VERIFIER_RUBRIC_VERSION = "semantic_task_authority_boundary.v1"
VERIFIER_OUTPUT_SCHEMA = VERIFIER_CONTRACT

_BLOCK_DECISIONS = {"block", "quarantine", "reject"}
_WARN_DECISIONS = {"warn", "route_to_review", "review"}
_PASS_DECISIONS = {"pass", "allow", "ok"}
_FORBIDDEN_MUTATION_KEYS = {
    "approve",
    "approved",
    "apply",
    "applied",
    "activate_edge",
    "create_bead",
    "create_goal",
    "write_bead",
    "write_memory",
    "write_soul",
}


def _clean_text(value: Any, *, max_len: int = 700) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > max_len:
        return text[: max(0, max_len - 3)].rstrip() + "..."
    return text


def _string_list(value: Any, *, limit: int = 12) -> list[str]:
    if isinstance(value, str):
        return [_clean_text(value)] if value.strip() else []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _clean_text(item, max_len=360)
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _truthy_mutation_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, "", [], {}):
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "no", "none", "0"}
    return True


def _find_forbidden_mutation_keys(value: Any, *, prefix: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if str(key).strip().lower() in _FORBIDDEN_MUTATION_KEYS and _truthy_mutation_value(item):
                hits.append(path)
            hits.extend(_find_forbidden_mutation_keys(item, prefix=path))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            hits.extend(_find_forbidden_mutation_keys(item, prefix=f"{prefix}[{idx}]"))
    return hits[:12]


def _deterministic_checks(
    *,
    output_json: Any,
    output_schema: str,
    authority_boundary: str,
    evidence_refs: list[Any],
    required_top_level_fields: list[str],
) -> dict[str, Any]:
    warnings: list[str] = []
    blocking_errors: list[str] = []

    if not isinstance(output_json, dict):
        return {
            "ok": False,
            "decision": "block",
            "status": "blocked",
            "warnings": [],
            "blocking_errors": ["missing_structured_output"],
        }

    if output_schema:
        contract = str(output_json.get("contract") or "").strip()
        if contract and contract != output_schema:
            blocking_errors.append(f"contract_mismatch:{contract}!={output_schema}")
        elif not contract:
            warnings.append("missing_contract")

    for field in required_top_level_fields:
        if field not in output_json:
            blocking_errors.append(f"missing_required_field:{field}")

    if not evidence_refs:
        warnings.append("missing_evidence_refs")

    boundary = str(authority_boundary or "").strip().lower()
    if boundary not in {"candidate_only", "advisory"}:
        warnings.append(f"unknown_authority_boundary:{boundary or 'missing'}")

    forbidden = _find_forbidden_mutation_keys(output_json)
    if forbidden:
        blocking_errors.append("authority_mismatch_forbidden_mutation_keys:" + ",".join(forbidden))

    return {
        "ok": not blocking_errors,
        "decision": "block" if blocking_errors else ("warn" if warnings else "pass"),
        "status": "blocked" if blocking_errors else ("warned" if warnings else "passed"),
        "warnings": warnings,
        "blocking_errors": blocking_errors,
    }


def _prompt() -> str:
    return (
        "You are the Core Memory semantic task verifier. Review the task output "
        "against its schema, evidence refs, and authority boundary. Return JSON only.\n\n"
        "Authority boundary: advisory. You cannot approve truth, apply SOUL, "
        "create beads, activate graph edges, or otherwise mutate canonical memory. "
        "You may only return pass, warn, route_to_review, or block.\n\n"
        "Return this shape:\n"
        "{\n"
        '  "contract": "memory.semantic_task_verifier.v1",\n'
        '  "decision": "pass|warn|route_to_review|block",\n'
        '  "warnings": ["..."],\n'
        '  "blocking_errors": ["..."],\n'
        '  "reason": "short explanation"\n'
        "}"
    )


def _decision_from_output(output_json: Any) -> tuple[str, list[str], list[str], str]:
    if not isinstance(output_json, dict):
        return "warn", ["verifier_output_unparsed"], [], ""
    decision = str(output_json.get("decision") or "").strip().lower()
    if decision in _PASS_DECISIONS:
        normalized = "pass"
    elif decision in _WARN_DECISIONS:
        normalized = "warn"
    elif decision in _BLOCK_DECISIONS:
        normalized = "block"
    else:
        normalized = "warn"
    return (
        normalized,
        _string_list(output_json.get("warnings")),
        _string_list(output_json.get("blocking_errors")),
        _clean_text(output_json.get("reason") or "", max_len=500),
    )


def _task_ref(task_id: str, receipt_id: str) -> dict[str, str]:
    return {
        "task_type": TASK_VERIFIER,
        "task_id": str(task_id or ""),
        "receipt_id": str(receipt_id or ""),
        "role": "semantic_task_verifier",
    }


def verify_semantic_task_output(
    *,
    root: str,
    source_task_type: str,
    source_task_id: str,
    source_receipt_id: str = "",
    output_schema: str = "",
    output_json: Any,
    authority_boundary: str = "advisory",
    evidence_refs: list[Any] | None = None,
    required_top_level_fields: list[str] | None = None,
    policy_rubric: str = "",
    require_semantic_verifier: bool = False,
    runtime: SemanticTaskRuntime | None = None,
) -> dict[str, Any]:
    """Verify a semantic task output and return pass/warn/block diagnostics."""

    refs = list(evidence_refs or [])
    deterministic = _deterministic_checks(
        output_json=output_json,
        output_schema=str(output_schema or ""),
        authority_boundary=str(authority_boundary or "advisory"),
        evidence_refs=refs,
        required_top_level_fields=list(required_top_level_fields or []),
    )
    if not deterministic.get("ok"):
        return {
            "ok": False,
            "status": "blocked",
            "decision": "block",
            "warnings": list(deterministic.get("warnings") or []),
            "blocking_errors": list(deterministic.get("blocking_errors") or []),
            "deterministic": deterministic,
            "task_ref": {},
        }

    payload = {
        "contract": VERIFIER_CONTRACT,
        "source_task_type": str(source_task_type or ""),
        "source_task_id": str(source_task_id or ""),
        "source_receipt_id": str(source_receipt_id or ""),
        "output_schema": str(output_schema or ""),
        "authority_boundary": str(authority_boundary or "advisory"),
        "policy_rubric": str(policy_rubric or ""),
        "evidence_refs": refs,
        "deterministic_verification": deterministic,
        "output_json": output_json if isinstance(output_json, dict) else {},
    }

    runner = runtime or get_semantic_task_runtime()
    result = runner.run(
        SemanticTaskRequest(
            root=str(root or ""),
            task_type=TASK_VERIFIER,
            prompt=_prompt() + "\n\nContext JSON:\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True),
            payload=payload,
            idempotency_key=f"verifier:{source_task_type}:{source_task_id}",
            prompt_version=VERIFIER_PROMPT_VERSION,
            rubric_version=VERIFIER_RUBRIC_VERSION,
            output_schema=VERIFIER_OUTPUT_SCHEMA,
            max_tokens=700,
            temperature=0.0,
            json_mode=True,
            fallback_mode="deterministic_verifier",
            authority_boundary="advisory",
            evidence_refs=refs,
            metadata={
                "source_task_type": str(source_task_type or ""),
                "source_task_id": str(source_task_id or ""),
                "source_receipt_id": str(source_receipt_id or ""),
            },
        )
    )

    ref = _task_ref(str(result.task_id or ""), str(result.receipt_id or ""))
    if not result.ok:
        warnings = list(deterministic.get("warnings") or [])
        warnings.append(str(result.error or "verifier_unavailable"))
        return {
            "ok": not require_semantic_verifier,
            "status": "unavailable" if not require_semantic_verifier else "blocked",
            "decision": "warn" if not require_semantic_verifier else "block",
            "warnings": warnings,
            "blocking_errors": ["semantic_verifier_unavailable"] if require_semantic_verifier else [],
            "deterministic": deterministic,
            "task_ref": ref,
            "task_id": str(result.task_id or ""),
            "receipt_id": str(result.receipt_id or ""),
            "error": str(result.error or ""),
        }

    decision, warnings, blocking_errors, reason = _decision_from_output(result.output_json)
    warnings = list(deterministic.get("warnings") or []) + warnings
    if decision == "block":
        blocking_errors = blocking_errors or ["semantic_verifier_blocked_output"]
    ok = decision != "block"
    return {
        "ok": ok,
        "status": "blocked" if not ok else ("warned" if warnings or decision == "warn" else "passed"),
        "decision": decision,
        "warnings": warnings,
        "blocking_errors": blocking_errors,
        "reason": reason,
        "deterministic": deterministic,
        "task_ref": ref,
        "task_id": str(result.task_id or ""),
        "receipt_id": str(result.receipt_id or ""),
    }


__all__ = [
    "VERIFIER_CONTRACT",
    "VERIFIER_OUTPUT_SCHEMA",
    "VERIFIER_PROMPT_VERSION",
    "VERIFIER_RUBRIC_VERSION",
    "verify_semantic_task_output",
]
