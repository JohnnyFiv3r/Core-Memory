from __future__ import annotations

import logging
import os
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.claim.outcomes import classify_memory_outcome
from core_memory.claim.turn_integration import extract_and_attach_claims
from core_memory.claim.update_policy import emit_claim_updates
from core_memory.config.feature_flags import (
    agent_authored_repair_enabled,
    agent_min_semantic_associations_after_first,
    claim_layer_enabled,
    resolved_agent_authored_gate,
)

from ..association.crawler_contract import (
    build_crawler_context,
    merge_crawler_updates,
)
from ..persistence.store import MemoryStore
from ..policy.bead_judge import judge_bead_fields
from ..policy.bead_typing import CLASSIFIABLE_TYPES, is_retrieval_turn
from ..policy.turn_memory_authoring import repair_turn_memory
from ..retrieval.lifecycle import mark_turn_checkpoint
from ..schema.agent_authored_updates import (
    AGENT_AUTHORED_UPDATES_V1,
    AGENT_AUTHORED_V1_BEAD_FIELDS,
    AgentAuthoredUpdatesV1,
    AuthoringMode,
    drop_unknown_agent_authored_updates_v1,
    validate_agent_authored_updates_v1_transport,
)
from ..schema.turn import Turn, reject_legacy_turn_kwargs
from ..write_pipeline.continuity_injection import load_continuity_injection
from .flush.flush_flow import process_flush_impl
from .passes.agent_authored_contract import (
    ERROR_AGENT_CALLABLE_MISSING,
    ERROR_AGENT_INVOCATION_EXHAUSTED,
    ERROR_AGENT_SEMANTIC_COVERAGE_MISSING,
    ERROR_AGENT_UPDATES_INVALID,
    ERROR_AGENT_UPDATES_MISSING,
    validate_agent_authored_updates,
)
from .passes.agent_crawler_invoke import invoke_turn_crawler_agent
from .passes.association_pass import run_association_pass
from .passes.decision_pass import run_session_decision_pass
from .queue.worker import SidecarPolicy, process_memory_event
from .session.live_session import read_live_session_beads
from .session.session_start_flow import process_session_start_impl
from .state import mark_memory_pass, try_claim_memory_pass
from .turn.ingress import maybe_emit_finalize_memory_event
from .turn.receipt import build_turn_finalized_receipt
from .turn.turn_flow import process_turn_finalized_impl
from .turn.turn_prep import infer_semantic_bead_type as _infer_semantic_bead_type
from .turn.turn_prep import normalize_turn_request as _normalize_turn_request
from .turn.turn_quality import emit_agent_turn_quality_metric as _emit_agent_turn_quality_metric

logger = logging.getLogger(__name__)

SEMANTIC_FIELDS = tuple(sorted(AGENT_AUTHORED_V1_BEAD_FIELDS - {"creation_role", "source_turn_ids"}))

_ALLOWED_BEAD_TYPES = set(CLASSIFIABLE_TYPES)


def _req_judge_directive(req: dict[str, Any] | None) -> str | None:
    """Return per-request judge directive, or None to fall back to env."""
    if str((req or {}).get("authoring_mode") or "").strip().lower() == "delegated":
        # The legacy bead_judge=llm directive is normalized to the full-schema
        # delegated author. Do not run the narrow field filler a second time.
        return "off"
    md = dict((req or {}).get("metadata") or {})
    val = str((req or {}).get("_bead_judge") or md.get("bead_judge") or "").strip().lower()
    return val or None  # "llm" | "heuristic" | "off" | None


def _judge_fallback_enabled(req: dict[str, Any] | None = None) -> bool:
    d = _req_judge_directive(req)
    if d is not None:
        return d in {"llm", "heuristic", "1", "true", "on"}
    return str(os.getenv("CORE_MEMORY_BEAD_JUDGE_FALLBACK", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _field_present(row: dict[str, Any], field: str) -> bool:
    if field not in row:
        return False
    value = row.get(field)
    if isinstance(value, bool):
        return True
    if isinstance(value, (list, tuple)):
        return len(value) > 0
    if value is None:
        return False
    return bool(str(value).strip())


def _maybe_apply_judge_fallback(
    row: dict[str, Any],
    user_query: str,
    assistant_final: str,
    *,
    req: dict[str, Any] | None = None,
    root: str | None = None,
) -> dict[str, Any]:
    if not _judge_fallback_enabled(req):
        return row
    out = dict(row)
    judged = judge_bead_fields(
        user_query=user_query,
        assistant_final=assistant_final,
        mode=_req_judge_directive(req),
        root=root,
    )
    for field in SEMANTIC_FIELDS:
        if not _field_present(out, field) and judged.get(field) is not None:
            out[field] = judged.get(field)
    tags = [str(x) for x in (out.get("tags") or []) if str(x).strip()]
    if "bead_judge_fallback" not in tags:
        tags.append("bead_judge_fallback")
    judge_tag = "llm_judged" if (judged.get("judge") or {}).get("mode") == "llm" else "heuristic_judged"
    if judge_tag not in tags:
        tags.append(judge_tag)
    out["tags"] = tags
    return out


# Canonical runtime center.
# P6A Step 2: deepen ownership by making memory_engine responsible for
# orchestration while delegating phase internals to dedicated lifecycle modules.


def _session_visible_bead_ids(root: str, session_id: str) -> list[str]:
    s = MemoryStore(root=root)
    idx = s._read_json(Path(root) / ".beads" / "index.json")
    out = []
    for bid, bead in (idx.get("beads") or {}).items():
        if str((bead or {}).get("session_id") or "") == str(session_id):
            out.append(str(bid))
    out.sort()
    return out


def _turn_judge_inputs(req: dict[str, Any]) -> tuple[str, str]:
    """Return text inputs for semantic judging, including N-speaker turns.

    Multi-speaker transcript rows can legitimately have no user/assistant role;
    in that case the canonical compatibility fields are empty but turn_text is
    the semantic content. Without this fallback all role=other rows collapse to
    generic "turn memory" beads.
    """
    user_query = str(req.get("user_query") or "").strip()
    assistant_final = str(req.get("assistant_final") or "").strip()
    if not user_query and not assistant_final:
        assistant_final = str(req.get("turn_text") or "").strip()
    return user_query, assistant_final


def _structural_turn_bead(
    req: dict[str, Any],
    *,
    tag: str = "seeded_by_engine",
    root: str | None = None,
) -> dict[str, Any]:
    user_query, assistant_final = _turn_judge_inputs(req)
    text = (user_query or assistant_final or "turn memory").strip()
    title = (text.splitlines()[0] if text else "Turn memory")[:160] or "Turn memory"
    summary = [text[:240] or "turn memory"]
    # Structural fallback may preserve continuity but cannot opt a bead into
    # retrieval. Missing compatibility values default false; semantic authorship
    # must explicitly request true.
    durable = bool(text) and not is_retrieval_turn(user_query)
    tags = ["crawler_reviewed", "turn_finalized", tag]
    if not durable:
        tags.append("semantic_fallback_disabled")
    return {
        "type": _infer_semantic_bead_type(user_query, assistant_final, root=root),
        "title": title,
        "summary": summary,
        "because": [],
        "source_turn_ids": [str(req.get("turn_id") or "")],
        "source_turn_ref": dict(req.get("source_turn_ref") or {}),
        "entities": _default_entities_from_text(user_query, assistant_final),
        "topics": [],
        "supporting_facts": [],
        "evidence_refs": [],
        "state_change": "",
        "validity": "",
        "retrieval_eligible": False,
        "effective_from": "",
        "effective_to": "",
        "observed_at": "",
        "tags": tags,
        "detail": (assistant_final or text)[:1200],
    }


def _judged_turn_bead(req: dict[str, Any], *, root: str | None = None) -> dict[str, Any]:
    user_query, assistant_final = _turn_judge_inputs(req)
    judged = judge_bead_fields(
        user_query=user_query,
        assistant_final=assistant_final,
        mode=_req_judge_directive(req),
        root=root,
    )
    req["_judged_claims"] = list(judged.get("claims") or [])
    bead = {
        key: deepcopy(value)
        for key, value in judged.items()
        if key in AGENT_AUTHORED_V1_BEAD_FIELDS and key not in {"creation_role", "source_turn_ids"}
    }
    bead.update(
        {
            "creation_role": "current_turn",
            "type": str(judged.get("type") or "context"),
            "title": str(judged.get("title") or "Turn memory"),
            "summary": list(judged.get("summary") or ["turn memory"]),
            "source_turn_ids": [str(req.get("turn_id") or "")],
            "source_turn_ref": dict(req.get("source_turn_ref") or {}),
            "tags": list(
                dict.fromkeys(
                    list(judged.get("tags") or [])
                    + [
                        "crawler_reviewed",
                        "turn_finalized",
                        "bead_judge_fallback",
                        ("llm_judged" if (judged.get("judge") or {}).get("mode") == "llm" else "heuristic_judged"),
                    ]
                )
            ),
            "detail": str(judged.get("detail") or "")[:1200],
        }
    )
    return bead


def _default_crawler_updates(req: dict[str, Any], *, root: str | None = None) -> dict[str, Any]:
    bead = _judged_turn_bead(req, root=root) if _judge_fallback_enabled(req) else _structural_turn_bead(req, root=root)
    return {"beads_create": [bead]}


def _default_entities_from_text(*texts: str, limit: int = 16) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    stop = {
        "Before",
        "After",
        "Turn",
        "Act",
        "Show",
        "Open",
        "Record",
        "Add",
        "Explain",
        "What",
        "When",
        "Where",
        "Which",
        "Why",
        "How",
        "Claims",
        "Graph",
        "Entities",
        "Runtime",
        "Benchmark",
        "Send",
        "Point",
        "Say",
        "The",
        "These",
        "This",
        "Those",
    }
    for raw in texts:
        text = str(raw or "")
        if not text:
            continue
        for m in re.finditer(r"\b([A-Z][A-Za-z0-9._-]{2,}|[A-Z]{2,}[A-Za-z0-9._-]*)\b", text):
            token = str(m.group(1) or "").strip().strip(".,:;!?()[]{}\"'")
            if len(token) < 3:
                continue
            if token in stop:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
            if len(out) >= max(1, int(limit)):
                return out

    # No all-words fallback: a structural bead with zero entities is honest;
    # lowercase sentence fragments promoted to entities become junk entity
    # worldlines and junk storyline backbones downstream.
    return out


def _resolve_reviewed_updates(
    req: dict[str, Any],
    *,
    root: str | None = None,
    source_override: str | None = None,
    invocation_diag: dict[str, Any] | None = None,
    max_create_per_turn: int | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    md = req.get("metadata") or {}
    reviewed = req.get("crawler_updates")
    if not isinstance(reviewed, dict):
        reviewed = md.get("crawler_updates") if isinstance(md, dict) else None
    resolved_gate = resolved_agent_authored_gate()
    required = bool(resolved_gate.get("required"))
    fail_open = bool(resolved_gate.get("fail_open"))
    mode = str(resolved_gate.get("mode") or "observe")

    gate = {
        "required": bool(required),
        "fail_open": bool(fail_open),
        "mode": mode,
        "source": str(
            source_override
            or (
                str(req.get("_crawler_updates_source") or "metadata.crawler_updates")
                if isinstance(reviewed, dict) and reviewed
                else "default_fallback"
            )
        ),
        "used_fallback": False,
        "blocked": False,
        "error_code": None,
        "agent_invocation": dict(invocation_diag or {}),
        "authorship": dict(req.get("authorship_provenance") or {}),
        "warnings": list(req.get("authorship_warnings") or []),
    }
    if isinstance((invocation_diag or {}).get("authorship"), dict):
        gate["authorship"] = dict((invocation_diag or {})["authorship"])

    is_v1 = isinstance(reviewed, dict) and reviewed.get("schema_version") == AGENT_AUTHORED_UPDATES_V1
    if is_v1 and not str((gate.get("authorship") or {}).get("source") or "").strip():
        # A typed inline payload is itself primary-agent authorship.  Delegated
        # calls receive richer task provenance above; this fallback preserves a
        # source for direct Python/HTTP/MCP inline submissions.
        gate["authorship"] = {
            "source": "delegated_semantic_agent"
            if str(req.get("authoring_mode") or "").strip().lower() == "delegated"
            else "inline_agent",
            "schema_version": AGENT_AUTHORED_UPDATES_V1,
        }
    if is_v1:
        transport_ok, transport_errors = validate_agent_authored_updates_v1_transport(reviewed)
        gate["transport_validation"] = {"ok": transport_ok, "errors": transport_errors}
        if not transport_ok and not required:
            reviewed, dropped_fields = drop_unknown_agent_authored_updates_v1(reviewed)
            gate["warnings"].append(
                {
                    "code": "agent_authored_updates_v1_compatibility_validation",
                    "errors": transport_errors,
                }
            )
            gate["warnings"].extend(
                {
                    "code": "unknown_authored_field_dropped",
                    "path": dropped["path"],
                    "field": dropped["field"],
                }
                for dropped in dropped_fields
            )

    if isinstance(reviewed, dict) and reviewed:
        if required or is_v1:
            ok, code, details = validate_agent_authored_updates(
                reviewed,
                max_create_per_turn=max_create_per_turn,
                turn_id=str(req.get("turn_id") or ""),
                require_v1=required,
            )
            gate["validation"] = details
            if not ok:
                gate["error_code"] = code
                if required:
                    gate["blocked"] = True
                    return None, gate
                gate["source"] = "default_fallback"
                gate["used_fallback"] = True
                gate["warnings"].append(
                    {
                        "code": "invalid_v1_authorship_fell_back_in_warn_mode",
                        "error_code": code,
                        "details": details,
                    }
                )
                return _default_crawler_updates(req, root=root), gate
        return dict(reviewed), gate

    if required:
        if isinstance(invocation_diag, dict) and invocation_diag.get("error_code") in {
            ERROR_AGENT_INVOCATION_EXHAUSTED,
            ERROR_AGENT_CALLABLE_MISSING,
        }:
            gate["error_code"] = str(invocation_diag.get("error_code") or ERROR_AGENT_UPDATES_MISSING)
        else:
            gate["error_code"] = (
                ERROR_AGENT_UPDATES_INVALID
                if req.get("_crawler_updates_source") or (isinstance(md, dict) and "crawler_updates" in md)
                else ERROR_AGENT_UPDATES_MISSING
            )
        gate["blocked"] = True
        return None, gate

    gate["source"] = "default_fallback"
    gate["used_fallback"] = True
    return _default_crawler_updates(req, root=root), gate


def _enforce_structural_invariants(root: str, req: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Enforce structural row invariants without re-authoring semantics."""
    out = dict(row)
    now = datetime.now(timezone.utc).isoformat()
    turn_id = str(req.get("turn_id") or "").strip()
    session_id = str(req.get("session_id") or "").strip()
    role = str(out.get("creation_role") or "current_turn").strip().lower()
    if role not in {"current_turn", "derived"}:
        role = "current_turn"
    out["creation_role"] = role

    if not str(out.get("bead_id") or "").strip():
        out["bead_id"] = f"bead-{uuid.uuid4().hex[:12].upper()}"

    bead_type = str(out.get("type") or "").strip().lower()
    out["type"] = bead_type if bead_type in _ALLOWED_BEAD_TYPES else "context"

    if not str(out.get("created_at") or "").strip():
        out["created_at"] = now
    if turn_id:
        out["turn_id"] = str(out.get("turn_id") or turn_id)
        src = [str(x) for x in (out.get("source_turn_ids") or []) if str(x).strip()]
        if role == "current_turn" and turn_id not in src:
            src.append(turn_id)
        elif role == "derived":
            src = [source_id for source_id in src if source_id != turn_id]
        out["source_turn_ids"] = src
    if role == "derived":
        derived_from = [str(item) for item in (out.get("derived_from_bead_ids") or []) if str(item).strip()]
        if "$current_turn" not in derived_from:
            derived_from.append("$current_turn")
        out["derived_from_bead_ids"] = derived_from
    if session_id and not str(out.get("session_id") or "").strip():
        out["session_id"] = session_id
    if not out.get("source_turn_ref"):
        out["source_turn_ref"] = {
            "turn_id": turn_id,
            "session_id": session_id,
            "speakers": list(req.get("speakers") or []),
        }

    return out


def _attach_runtime_creation_fields(req: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Attach identifiers and source metadata without changing authored meaning."""

    out = dict(row)
    if not str(out.get("bead_id") or "").strip():
        out["bead_id"] = f"bead-{uuid.uuid4().hex[:12].upper()}"
    if not str(out.get("created_at") or "").strip():
        out["created_at"] = datetime.now(timezone.utc).isoformat()
    out["session_id"] = str(req.get("session_id") or "")
    out["turn_id"] = str(req.get("turn_id") or "")
    out["source_turn_ref"] = dict(
        req.get("source_turn_ref")
        or {
            "turn_id": str(req.get("turn_id") or ""),
            "session_id": str(req.get("session_id") or ""),
            "speakers": list(req.get("speakers") or []),
        }
    )
    return out


def _ensure_turn_creation_update(
    root: str,
    req: dict[str, Any],
    updates: dict[str, Any],
    *,
    strict_contract: bool = False,
) -> dict[str, Any]:
    """Guarantee and structurally attach one canonical current-turn candidate."""
    out = dict(updates or {})
    key = "beads_create"
    rows = list(out.get(key) or [])
    turn_id = str(req.get("turn_id") or "")

    if strict_contract or str(out.get("schema_version") or "") == AGENT_AUTHORED_UPDATES_V1:
        # Validation already proved cardinality, roles, and grounding. The
        # runtime may attach only its owned identifiers here; it must not fill,
        # coerce, or pass a primary-agent row through the bead judge.
        out[key] = [_attach_runtime_creation_fields(req, row) for row in rows if isinstance(row, dict)]
        return out

    explicit_primary_indices = [
        index
        for index, row in enumerate(rows)
        if isinstance(row, dict) and str(row.get("creation_role") or "").strip().lower() == "current_turn"
    ]
    first_object_index = next(
        (index for index, row in enumerate(rows) if isinstance(row, dict)),
        None,
    )
    primary_index = explicit_primary_indices[0] if explicit_primary_indices else first_object_index

    has_turn = False
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        prepared = dict(row)
        explicit_role = str(prepared.get("creation_role") or "").strip().lower()
        if i == primary_index:
            prepared["creation_role"] = "current_turn"
        elif explicit_role != "current_turn":
            prepared["creation_role"] = "derived"
        rows[i] = _enforce_structural_invariants(root, req, prepared)
        user_query, assistant_final = _turn_judge_inputs(req)
        if str(rows[i].get("creation_role") or "") == "current_turn":
            rows[i] = _maybe_apply_judge_fallback(rows[i], user_query, assistant_final, req=req, root=root)
        src = [str(x) for x in (rows[i].get("source_turn_ids") or []) if str(x)]
        if str(rows[i].get("creation_role") or "") == "current_turn" and turn_id and turn_id in src:
            has_turn = True

    if not has_turn:
        bead = (
            _judged_turn_bead(req, root=root) if _judge_fallback_enabled(req) else _structural_turn_bead(req, root=root)
        )
        bead["creation_role"] = "current_turn"
        bead["source_turn_ids"] = [turn_id]
        bead["source_turn_ref"] = dict(
            req.get("source_turn_ref")
            or {
                "turn_id": turn_id,
                "session_id": req.get("session_id"),
                "speakers": list(req.get("speakers") or []),
            }
        )
        rows.append(bead)

    out[key] = rows
    return out


def _queue_preview_associations(root: str, session_id: str, visible_bead_ids: list[str]) -> int:
    """association_preview has been removed — associations are now real records only.

    This stub remains for call-site compatibility but always returns 0.
    """
    return 0


def _non_temporal_semantic_association_count(updates: dict[str, Any]) -> int:
    associations = list((updates or {}).get("associations") or [])
    # Keep generic/temporal/noise preview labels out of semantic-count gating.
    # Rich canonical relations emitted by association.preview (for example
    # caused_by/led_to/supports) intentionally remain counted.
    excluded = {"follows", "precedes", "shared_tag", "associated_with"}
    count = 0
    for row in associations:
        if not isinstance(row, dict):
            continue
        rel = str(row.get("relationship") or "").strip().lower()
        if rel and rel not in excluded:
            count += 1
    return count


def process_turn_finalized(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    transaction_id: str | None = None,
    trace_id: str | None = None,
    turns: list[Turn | dict[str, Any]] | None = None,
    trace_depth: int = 0,
    origin: str = "USER_TURN",
    tools_trace: list[dict] | None = None,
    mesh_trace: list[dict] | None = None,
    window_turn_ids: list[str] | None = None,
    window_bead_ids: list[str] | None = None,
    crawler_updates: AgentAuthoredUpdatesV1 | None = None,
    authoring_mode: AuthoringMode | None = None,
    metadata: dict[str, Any] | None = None,
    policy: SidecarPolicy | None = None,
    _authorship_provenance: dict[str, Any] | None = None,
    **legacy_kwargs: Any,
) -> dict[str, Any]:
    """Canonical adapter `on_turn_end` boundary.

    This is the public lifecycle hook for a completed host-runtime turn. See
    `docs/adapters/contract.md` for required/optional adapter fields, timing,
    idempotency, and adapter/runtime responsibility boundaries.
    """
    reject_legacy_turn_kwargs(legacy_kwargs, surface="process_turn_finalized")
    result = process_turn_finalized_impl(
        root=root,
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id,
        turns=turns,
        trace_depth=trace_depth,
        origin=origin,
        tools_trace=tools_trace,
        mesh_trace=mesh_trace,
        window_turn_ids=window_turn_ids,
        window_bead_ids=window_bead_ids,
        crawler_updates=crawler_updates,
        authoring_mode=authoring_mode,
        metadata=metadata,
        trusted_authorship_provenance=_authorship_provenance,
        policy=policy,
        normalize_turn_request=_normalize_turn_request,
        mark_turn_checkpoint=mark_turn_checkpoint,
        maybe_emit_finalize_memory_event=maybe_emit_finalize_memory_event,
        build_crawler_context=build_crawler_context,
        invoke_turn_crawler_agent=invoke_turn_crawler_agent,
        resolve_reviewed_updates=lambda req, **kwargs: _resolve_reviewed_updates(req, root=root, **kwargs),
        repair_enabled=bool(agent_authored_repair_enabled() or getattr(policy, "semantic_repair_enabled", False)),
        repair_turn_memory_fn=repair_turn_memory,
        emit_agent_turn_quality_metric=_emit_agent_turn_quality_metric,
        session_visible_bead_ids=_session_visible_bead_ids,
        non_temporal_semantic_association_count=_non_temporal_semantic_association_count,
        agent_min_semantic_associations_after_first=agent_min_semantic_associations_after_first,
        try_claim_memory_pass=try_claim_memory_pass,
        mark_memory_pass=mark_memory_pass,
        process_memory_event=process_memory_event,
        default_crawler_updates=lambda req: _default_crawler_updates(req, root=root),
        ensure_turn_creation_update=_ensure_turn_creation_update,
        run_association_pass=run_association_pass,
        queue_preview_associations=_queue_preview_associations,
        merge_crawler_updates=merge_crawler_updates,
        run_session_decision_pass=run_session_decision_pass,
        error_agent_updates_missing=ERROR_AGENT_UPDATES_MISSING,
        error_agent_semantic_coverage_missing=ERROR_AGENT_SEMANTIC_COVERAGE_MISSING,
        logger=logger,
        extract_and_attach_claims_fn=extract_and_attach_claims if claim_layer_enabled() else None,
        emit_claim_updates_fn=emit_claim_updates if claim_layer_enabled() else None,
        classify_memory_outcome_fn=classify_memory_outcome if claim_layer_enabled() else None,
    )

    # F-W1: drain enrichment queue after critical path returns
    if result.get("ok") and result.get("enrichment_queued"):
        try:
            from core_memory.runtime.queue.side_effect_queue import drain_side_effect_queue

            # Automatic bead-commit hooks can enqueue association-pass work
            # before this turn-enrichment item. Drain a small bounded batch so
            # the post-turn enrichment job is not starved by earlier hook jobs.
            drain_out = drain_side_effect_queue(root=root, max_items=10)
            result["enrichment_drain"] = drain_out
            for item in list(drain_out.get("results") or []):
                if not isinstance(item, dict) or item.get("kind") != "turn-enrichment":
                    continue
                processor_result = item.get("result") if isinstance(item.get("result"), dict) else {}
                enriched = (
                    processor_result.get("result")
                    if isinstance(processor_result.get("result"), dict)
                    else processor_result
                )
                handoff = result.setdefault("crawler_handoff", {})
                if isinstance(enriched, dict):
                    if enriched.get("auto_apply") is not None:
                        handoff["auto_apply"] = enriched.get("auto_apply")
                        handoff["association_pass"] = enriched.get("auto_apply")
                    if enriched.get("merge") is not None:
                        handoff["merge"] = enriched.get("merge")
                        handoff["turn_merge"] = enriched.get("merge")
                    if enriched.get("decision_pass") is not None:
                        handoff["decision_pass"] = enriched.get("decision_pass")
                    if enriched.get("goal_lifecycle") is not None:
                        result["goal_lifecycle"] = enriched.get("goal_lifecycle")
                    assoc = enriched.get("association") if isinstance(enriched.get("association"), dict) else {}
                    if not result.get("bead_id"):
                        result["bead_id"] = str(assoc.get("current_turn_bead_id") or "")
            # On the queued path the canonical bead is created during the drain,
            # so the contract's bead_id is resolvable only now.
            if not result.get("bead_id"):
                try:
                    from core_memory.persistence.store_claim_ops import find_canonical_turn_bead_id as _find_bid

                    result["bead_id"] = str(
                        _find_bid(
                            root,
                            session_id=session_id,
                            turn_id=turn_id,
                            preferred_bead_ids=[],
                        )
                        or ""
                    )
                except Exception:
                    pass
            if claim_layer_enabled():
                try:
                    from core_memory.persistence.store_claim_ops import find_canonical_turn_bead_id

                    store = MemoryStore(root)
                    bid = find_canonical_turn_bead_id(
                        root,
                        session_id=session_id,
                        turn_id=turn_id,
                        preferred_bead_ids=[],
                    )
                    idx = store._read_json(store.beads_dir / "index.json")
                    bead = (idx.get("beads") or {}).get(str(bid or "")) or {}
                    result["memory_outcome_written"] = bool(bead.get("memory_outcome"))
                except Exception:
                    result["memory_outcome_written"] = False
        except Exception as exc:
            logger.warning("engine: turn semantic-write queue drain failed; durable item remains retryable: %s", exc)

    receipt = build_turn_finalized_receipt(
        root=root,
        session_id=session_id,
        turn_id=turn_id,
        result=result,
    )
    result.update(receipt)

    return result


def process_flush(
    *,
    root: str,
    session_id: str,
    promote: bool,
    token_budget: int,
    max_beads: int,
    source: str = "flush_hook",
    flush_tx_id: str | None = None,
    semantic_override: bool = False,
    override_operator: str = "",
    override_reason: str = "",
) -> dict[str, Any]:
    """Canonical adapter `on_session_end` boundary.

    This hook may fire one or more times per session: actual session end,
    threshold-triggered compaction, idle maintenance, or scheduled flush. See
    `docs/adapters/contract.md` for the adapter lifecycle contract.
    """
    flush_kwargs: dict[str, Any] = {
        "root": root,
        "session_id": session_id,
        "promote": promote,
        "token_budget": token_budget,
        "max_beads": max_beads,
        "source": source,
        "flush_tx_id": flush_tx_id,
    }
    if semantic_override or override_operator or override_reason:
        flush_kwargs.update(
            {
                "semantic_override": semantic_override,
                "override_operator": override_operator,
                "override_reason": override_reason,
            }
        )
    return process_flush_impl(
        **flush_kwargs,
    )


def read_live_session(*, root: str, session_id: str) -> dict[str, Any]:
    return read_live_session_beads(root, session_id)


def emit_turn_finalized(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    transaction_id: str | None = None,
    trace_id: str | None = None,
    turns: list[Turn | dict[str, Any]] | None = None,
    trace_depth: int = 0,
    origin: str = "USER_TURN",
    tools_trace: list[dict] | None = None,
    mesh_trace: list[dict] | None = None,
    window_turn_ids: list[str] | None = None,
    window_bead_ids: list[str] | None = None,
    crawler_updates: AgentAuthoredUpdatesV1 | None = None,
    authoring_mode: AuthoringMode | None = None,
    metadata: dict[str, Any] | None = None,
    **legacy_kwargs: Any,
) -> dict[str, Any]:
    reject_legacy_turn_kwargs(legacy_kwargs, surface="emit_turn_finalized")
    req = _normalize_turn_request(
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id,
        turns=turns,
        trace_depth=trace_depth,
        origin=origin,
        tools_trace=tools_trace,
        mesh_trace=mesh_trace,
        window_turn_ids=window_turn_ids,
        window_bead_ids=window_bead_ids,
        crawler_updates=crawler_updates,
        authoring_mode=authoring_mode,
        metadata=metadata,
    )
    out = maybe_emit_finalize_memory_event(
        root,
        session_id=req["session_id"],
        turn_id=req["turn_id"],
        transaction_id=req["transaction_id"],
        trace_id=req["trace_id"],
        user_query=req["user_query"],
        assistant_final=req["assistant_final"],
        turns=req["turns"],
        speakers=req["speakers"],
        trace_depth=req["trace_depth"],
        origin=req["origin"],
        tools_trace=req["tools_trace"],
        mesh_trace=req["mesh_trace"],
        window_turn_ids=req["window_turn_ids"],
        window_bead_ids=req["window_bead_ids"],
        crawler_updates=req["crawler_updates"],
        authoring_mode=req["authoring_mode"],
        metadata=req["metadata"],
    )
    out.setdefault("engine", {})
    out["engine"].update({"normalized": True, "entry": "emit_turn_finalized"})
    return out


def crawler_turn_context(
    *,
    root: str,
    session_id: str,
    limit: int = 200,
    carry_in_bead_ids: list[str] | None = None,
) -> dict[str, Any]:
    out = build_crawler_context(root=root, session_id=session_id, limit=limit, carry_in_bead_ids=carry_in_bead_ids)
    out.setdefault("engine", {})
    out["engine"].update({"entry": "crawler_turn_context"})
    return out


def apply_crawler_turn_updates(
    *, root: str, session_id: str, updates: dict[str, Any], visible_bead_ids: list[str] | None = None
) -> dict[str, Any]:
    out = run_association_pass(
        root=root,
        session_id=session_id,
        updates=updates,
        visible_bead_ids=visible_bead_ids,
    )
    out.setdefault("engine", {})
    out["engine"].update({"entry": "apply_crawler_turn_updates"})
    return out


def process_session_start(
    *,
    root: str,
    session_id: str,
    source: str = "runtime",
    max_items: int = 80,
    soul_subject: str = "self",
) -> dict[str, Any]:
    """Canonical adapter `on_session_start` boundary.

    Adapters should call this once before the first turn for a session; repeated
    calls are safe continuity refreshes. The result carries a read-only ``soul``
    payload (SOUL.md/GOALS.md/TENSIONS.md) for working-memory injection (§4.3).
    See `docs/adapters/contract.md` for the adapter lifecycle contract.
    """
    return process_session_start_impl(
        root=root, session_id=session_id, source=source, max_items=max_items, soul_subject=soul_subject
    )


def continuity_injection_context(*, workspace_root: str, max_items: int = 80) -> dict[str, Any]:
    out = load_continuity_injection(workspace_root=workspace_root, max_items=max_items)
    out.setdefault("engine", {})
    out["engine"].update({"entry": "continuity_injection_context"})
    return out
