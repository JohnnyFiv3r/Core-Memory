from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from core_memory.policy.bead_typing import classify_bead_type
from core_memory.schema.agent_authored_updates import (
    AgentAuthoredUpdatesV1,
    AuthoringMode,
    normalize_authoring_mode,
)
from core_memory.schema.turn import (
    Turn,
    assistant_content,
    normalize_turns,
    reject_legacy_turn_kwargs,
    serialize_turns,
    turn_speakers,
    turns_summary,
    user_content,
)


def normalize_turn_request(
    *,
    session_id: str,
    turn_id: str,
    transaction_id: str | None,
    trace_id: str | None,
    turns: list[Turn | dict[str, Any]] | None,
    trace_depth: int,
    origin: str,
    tools_trace: list[dict] | None,
    mesh_trace: list[dict] | None,
    window_turn_ids: list[str] | None,
    window_bead_ids: list[str] | None,
    crawler_updates: AgentAuthoredUpdatesV1 | None,
    authoring_mode: AuthoringMode | None,
    metadata: dict[str, Any] | None,
    **legacy_kwargs: Any,
) -> dict[str, Any]:
    """Canonical turn-finalized input normalization."""
    reject_legacy_turn_kwargs(legacy_kwargs, surface="process_turn_finalized")
    sid = str(session_id or "").strip()
    tid = str(turn_id or "").strip()
    tx = str(transaction_id or f"tx-{tid}-{uuid.uuid4().hex[:8]}")
    tr = str(trace_id or f"tr-{tid}-{uuid.uuid4().hex[:8]}")
    normalized_turns = normalize_turns(turns)
    speakers = turn_speakers(normalized_turns)
    md = dict(metadata or {})
    warnings: list[dict[str, Any]] = []
    typed_updates = deepcopy(crawler_updates) if isinstance(crawler_updates, dict) else None
    metadata_updates = md.get("crawler_updates") if isinstance(md.get("crawler_updates"), dict) else None
    if typed_updates is not None and metadata_updates is not None:
        warnings.append(
            {
                "code": "metadata_crawler_updates_ignored",
                "winner": "crawler_updates",
                "ignored": "metadata.crawler_updates",
            }
        )
    elif typed_updates is None and metadata_updates is not None:
        warnings.append(
            {
                "code": "metadata_crawler_updates_deprecated",
                "replacement": "crawler_updates",
            }
        )
    resolved_updates = typed_updates if typed_updates is not None else deepcopy(metadata_updates)
    updates_source = (
        "crawler_updates"
        if typed_updates is not None
        else ("metadata.crawler_updates" if metadata_updates is not None else "")
    )

    resolved_authoring_mode = normalize_authoring_mode(authoring_mode)
    judge_directive = str(md.get("bead_judge") or "").strip().lower()
    if resolved_authoring_mode is None and resolved_updates is not None:
        resolved_authoring_mode = "inline"
        warnings.append(
            {
                "code": "authoring_mode_defaulted_inline",
                "reason": "authored_updates_present",
            }
        )
    if resolved_authoring_mode is None and judge_directive in {"llm", "auto", "1", "true", "on"}:
        resolved_authoring_mode = "delegated"
        warnings.append(
            {
                "code": "bead_judge_directive_deprecated",
                "replacement": "authoring_mode=delegated",
            }
        )
    elif judge_directive in {"llm", "auto", "1", "true", "on"}:
        warnings.append(
            {
                "code": "bead_judge_directive_ignored",
                "reason": "typed_authorship_already_selected",
            }
        )
    if resolved_authoring_mode == "delegated" and resolved_updates is not None:
        warnings.append(
            {
                "code": "delegated_mode_ignored",
                "reason": "authored_updates_present",
            }
        )
        resolved_authoring_mode = "inline"

    authorship: dict[str, Any] = {}
    if resolved_updates is not None:
        authorship = {
            "source": "primary_agent",
            "schema_version": str(
                resolved_updates.get("schema_version")
                or ("legacy_unversioned" if updates_source == "metadata.crawler_updates" else "unversioned")
            ),
            "prompt_version": str(md.get("authoring_prompt_version") or ""),
            "model_profile": deepcopy(md.get("authoring_model_profile") or {}),
            "fallback_mode": "metadata_alias" if updates_source == "metadata.crawler_updates" else "none",
        }

    return {
        "session_id": sid,
        "turn_id": tid,
        "transaction_id": tx,
        "trace_id": tr,
        "turns": serialize_turns(normalized_turns),
        "speakers": speakers,
        # Derived compatibility fields for internal policies while the rest of the
        # pipeline moves to the canonical N-speaker shape.
        "user_query": user_content(normalized_turns),
        "assistant_final": assistant_content(normalized_turns),
        "turn_text": turns_summary(normalized_turns),
        "source_turn_ref": {"turn_id": tid, "session_id": sid, "speakers": speakers},
        "trace_depth": int(trace_depth or 0),
        "origin": str(origin or "USER_TURN"),
        "tools_trace": list(tools_trace or []),
        "mesh_trace": list(mesh_trace or []),
        "window_turn_ids": [str(x) for x in (window_turn_ids or [])],
        "window_bead_ids": [str(x) for x in (window_bead_ids or [])],
        "crawler_updates": resolved_updates,
        "authoring_mode": resolved_authoring_mode,
        "authorship_warnings": warnings,
        "authorship_provenance": authorship,
        "_crawler_updates_source": updates_source,
        "metadata": md,
    }


def infer_semantic_bead_type(user_query: str, assistant_final: str, *, root: str | None = None) -> str:
    """LLM-first bead type policy classifier with deterministic fallback."""
    return classify_bead_type(user_query=user_query, assistant_final=assistant_final, root=root)
