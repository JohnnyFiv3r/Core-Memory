"""Full-schema delegated semantic authoring for finalized turns."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from core_memory.schema.agent_authored_updates import (
    AGENT_AUTHORED_UPDATES_V1,
    AGENT_OWNED_BEAD_FIELDS,
    agent_authored_updates_json_schema,
    validate_agent_authored_updates_v1_transport,
)
from core_memory.schema.agent_authoring_spec import BEAD_AUTHORING_SPEC
from core_memory.schema.semantic_tasks import (
    TASK_TURN_MEMORY_AUTHORING,
    SemanticTaskRequest,
    SemanticTaskRuntime,
)

from .semantic_task_runtime import get_semantic_task_runtime

TURN_MEMORY_AUTHORING_PROMPT_VERSION = "turn_memory_authoring.v1"
TURN_MEMORY_AUTHORING_RUBRIC_VERSION = "agent_led_semantic_write.v1"

_VISIBLE_BEAD_FIELDS = frozenset(AGENT_OWNED_BEAD_FIELDS | {"id", "created_at", "status", "source_turn_ids"})


def _grounding_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bounded_visible_context(crawler_context: dict[str, Any]) -> dict[str, Any]:
    visible_ids = [str(item) for item in (crawler_context.get("visible_bead_ids") or []) if str(item)]
    beads: list[dict[str, Any]] = []
    for raw in list(crawler_context.get("beads") or [])[-50:]:
        if not isinstance(raw, dict):
            continue
        beads.append(
            {
                field: raw[field]
                for field in sorted(_VISIBLE_BEAD_FIELDS)
                if field in raw and (raw[field] not in (None, "", [], {}) or isinstance(raw[field], bool))
            }
        )
    return {
        "session_id": str(crawler_context.get("session_id") or ""),
        "visible_bead_ids": visible_ids[-200:],
        "beads": beads,
    }


def _authoring_payload(
    req: dict[str, Any],
    crawler_context: dict[str, Any],
    *,
    repair_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "session_id": str(req.get("session_id") or ""),
        "turn_id": str(req.get("turn_id") or ""),
        "turns": list(req.get("turns") or []),
        "speakers": list(req.get("speakers") or []),
        "tools_trace": list(req.get("tools_trace") or []),
        "mesh_trace": list(req.get("mesh_trace") or []),
        "window_turn_ids": list(req.get("window_turn_ids") or []),
        "window_bead_ids": list(req.get("window_bead_ids") or []),
        "visible_memory": _bounded_visible_context(crawler_context),
    }
    if isinstance(repair_context, dict):
        payload["repair_context"] = deepcopy(repair_context)
    return payload


def _prompt(payload: dict[str, Any], *, repair_mode: bool = False) -> str:
    contract_schema = agent_authored_updates_json_schema()
    return "\n\n".join(
        [
            BEAD_AUTHORING_SPEC,
            (
                "EXPLICIT REPAIR MODE: return a complete replacement agent_authored_updates.v1 payload. "
                "Preserve every valid primary-authored value unchanged. Add or change only fields required "
                "to satisfy the contract and grounding. Every changed field will be attributed to this repair task."
                if repair_mode
                else ""
            ),
            "Return JSON only. The complete machine-readable output schema follows:",
            json.dumps(contract_schema, ensure_ascii=False, sort_keys=True),
            "Grounding context follows. Author only claims supported by this context:",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        ]
    )


def build_turn_memory_authoring_request(
    *,
    root: str | None,
    req: dict[str, Any],
    crawler_context: dict[str, Any],
    task_type: str = TASK_TURN_MEMORY_AUTHORING,
    metadata: dict[str, Any] | None = None,
    additional_instructions: str = "",
    fallback_mode: str = "none",
    authority_boundary: str = "semantic_author",
    repair_context: dict[str, Any] | None = None,
    authorship_source: str = "delegated_semantic_agent",
) -> tuple[SemanticTaskRequest, str]:
    """Build one full-contract request for canonical or compatibility callers."""

    repair_mode = isinstance(repair_context, dict)
    payload = _authoring_payload(req, crawler_context, repair_context=repair_context)
    grounding_hash = _grounding_hash(payload)
    request = SemanticTaskRequest(
        task_type=task_type,
        root=root,
        prompt="\n\n".join(
            part
            for part in (
                _prompt(payload, repair_mode=repair_mode),
                (
                    "Compatibility operator guidance follows. It may refine semantic judgment but must not "
                    f"change the required {AGENT_AUTHORED_UPDATES_V1} output shape:\n{additional_instructions}"
                    if str(additional_instructions or "").strip()
                    else ""
                ),
            )
            if part
        ),
        payload=payload,
        idempotency_key=f"turn-memory-author:{req.get('session_id')}:{req.get('turn_id')}:{grounding_hash}",
        prompt_version=TURN_MEMORY_AUTHORING_PROMPT_VERSION,
        rubric_version=TURN_MEMORY_AUTHORING_RUBRIC_VERSION,
        output_schema=AGENT_AUTHORED_UPDATES_V1,
        max_tokens=4200,
        temperature=0,
        json_mode=True,
        fallback_mode=fallback_mode,
        authority_boundary=authority_boundary,
        evidence_refs=[str(req.get("turn_id") or "")],
        metadata={
            "policy": "turn_memory_authoring",
            "authorship_source": str(authorship_source),
            "grounding_hash": grounding_hash,
            "authoring_operation": "repair" if repair_mode else "author",
            **dict(metadata or {}),
        },
    )
    return request, grounding_hash


def author_turn_memory(
    *,
    root: str,
    req: dict[str, Any],
    crawler_context: dict[str, Any],
    task_runtime: SemanticTaskRuntime | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Run the canonical delegated author and return updates plus provenance."""

    return _run_turn_memory_authoring(
        root=root,
        req=req,
        crawler_context=crawler_context,
        task_runtime=task_runtime,
        authorship_source="delegated_semantic_agent",
    )


def _leaf_paths(value: Any, *, prefix: str = "$") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key in sorted(value):
            paths.extend(_leaf_paths(value[key], prefix=f"{prefix}.{key}"))
        return paths or [prefix]
    if isinstance(value, list):
        paths = []
        for index, item in enumerate(value):
            paths.extend(_leaf_paths(item, prefix=f"{prefix}[{index}]"))
        return paths or [prefix]
    return [prefix]


def _value_at_path(value: Any, path: str) -> tuple[bool, Any]:
    cursor = value
    token = ""
    index = 1
    while index < len(path):
        char = path[index]
        if char == ".":
            if token:
                if not isinstance(cursor, dict) or token not in cursor:
                    return False, None
                cursor = cursor[token]
                token = ""
            index += 1
            continue
        if char == "[":
            if token:
                if not isinstance(cursor, dict) or token not in cursor:
                    return False, None
                cursor = cursor[token]
                token = ""
            close = path.find("]", index)
            if close < 0:
                return False, None
            try:
                item_index = int(path[index + 1 : close])
            except ValueError:
                return False, None
            if not isinstance(cursor, list) or item_index >= len(cursor):
                return False, None
            cursor = cursor[item_index]
            index = close + 1
            continue
        token += char
        index += 1
    if token:
        if not isinstance(cursor, dict) or token not in cursor:
            return False, None
        cursor = cursor[token]
    return True, cursor


def repaired_field_paths(original: Any, repaired: dict[str, Any]) -> list[str]:
    """Return every repaired leaf whose value is new or changed."""

    changed: list[str] = []
    for path in _leaf_paths(repaired):
        found, prior = _value_at_path(original, path)
        found_after, current = _value_at_path(repaired, path)
        if found_after and (not found or prior != current):
            changed.append(path)
    for path in _leaf_paths(original):
        found_before, prior = _value_at_path(original, path)
        found, current = _value_at_path(repaired, path)
        if found_before and (not found or prior != current):
            changed.append(path)
    return sorted(set(changed))


def repair_turn_memory(
    *,
    root: str,
    req: dict[str, Any],
    crawler_context: dict[str, Any],
    invalid_updates: dict[str, Any] | None,
    validation: dict[str, Any],
    task_runtime: SemanticTaskRuntime | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Run an explicitly enabled, full-contract attributed repair task."""

    original = deepcopy(invalid_updates) if isinstance(invalid_updates, dict) else {}
    updates, diag = _run_turn_memory_authoring(
        root=root,
        req=req,
        crawler_context=crawler_context,
        task_runtime=task_runtime,
        authorship_source="repair_agent",
        repair_context={
            "invalid_updates": original,
            "validation": deepcopy(validation),
        },
    )
    authorship = dict(diag.get("authorship") or {})
    repaired_fields = repaired_field_paths(original, updates) if isinstance(updates, dict) else []
    repair_provenance = {
        "source": "repair_agent",
        "model_profile": deepcopy(authorship.get("model_profile") or {}),
        "prompt_version": str(authorship.get("prompt_version") or ""),
        "rubric_version": str(authorship.get("rubric_version") or ""),
        "schema_version": str(authorship.get("schema_version") or ""),
        "grounding_hash": str(authorship.get("grounding_hash") or ""),
        "task_id": str(authorship.get("task_id") or ""),
        "task_receipt_id": str(authorship.get("task_receipt_id") or ""),
    }
    authorship.update(
        {
            "source": "repair_agent",
            "repair_used": bool(updates),
            "repaired_fields": repaired_fields,
            "field_provenance": {path: deepcopy(repair_provenance) for path in repaired_fields},
            "primary_authorship": deepcopy(req.get("authorship_provenance") or {"source": "missing"}),
            "repair_authorship": repair_provenance,
        }
    )
    diag["authorship"] = authorship
    diag["repair_used"] = bool(updates)
    diag["repaired_fields"] = repaired_fields
    return updates, diag


def _run_turn_memory_authoring(
    *,
    root: str,
    req: dict[str, Any],
    crawler_context: dict[str, Any],
    task_runtime: SemanticTaskRuntime | None,
    authorship_source: str,
    repair_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Execute one canonical full-contract authoring task."""

    runtime = task_runtime or get_semantic_task_runtime()
    request, grounding_hash = build_turn_memory_authoring_request(
        root=root,
        req=req,
        crawler_context=crawler_context,
        repair_context=repair_context,
        authorship_source=authorship_source,
        authority_boundary="semantic_repair_agent" if repair_context is not None else "semantic_author",
    )
    result = runtime.run(request)

    model_profile = result.model_profile.as_dict() if result.model_profile else {}
    authorship = {
        "source": str(authorship_source),
        "model_profile": model_profile,
        "prompt_version": TURN_MEMORY_AUTHORING_PROMPT_VERSION,
        "rubric_version": TURN_MEMORY_AUTHORING_RUBRIC_VERSION,
        "schema_version": AGENT_AUTHORED_UPDATES_V1,
        "grounding_hash": grounding_hash,
        "task_id": result.task_id,
        "task_receipt_id": result.receipt_id,
        "task_status": result.status,
        "fallback_mode": result.fallback_mode,
    }
    diag: dict[str, Any] = {
        "attempted": True,
        "ok": False,
        "source": str(authorship_source),
        "attempts": 1,
        "error_code": None,
        "authorship": authorship,
    }
    if not result.ok:
        diag.update({"error_code": "delegated_semantic_author_unavailable", "error": result.error})
        return None, diag

    updates = result.output_json
    if isinstance(updates, dict) and isinstance(updates.get("crawler_updates"), dict):
        updates = updates["crawler_updates"]
        authorship["compatibility_unwrapped"] = "crawler_updates"
    ok, validation_errors = validate_agent_authored_updates_v1_transport(updates)
    authorship["validation"] = {"ok": ok, "errors": validation_errors}
    if not ok or not isinstance(updates, dict):
        diag.update(
            {
                "error_code": "delegated_semantic_author_invalid",
                "validation_errors": validation_errors,
            }
        )
        return None, diag

    diag["ok"] = True
    return dict(updates), diag


__all__ = [
    "TURN_MEMORY_AUTHORING_PROMPT_VERSION",
    "TURN_MEMORY_AUTHORING_RUBRIC_VERSION",
    "author_turn_memory",
    "build_turn_memory_authoring_request",
    "repair_turn_memory",
    "repaired_field_paths",
]
