from __future__ import annotations

"""Governed maintenance facade for agent control-plane operations.

The public ``maintain()`` verb intentionally routes through existing Core
Memory operations instead of exposing raw file/index mutation to agents.
"""

from typing import Any


MAINTAIN_CONTRACT = "core_memory.maintain.v1"


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _normalize_action(action: str) -> str:
    a = _clean_str(action).lower().replace("-", "_")
    aliases = {
        "delete_bead": "remove_beads",
        "delete_beads": "remove_beads",
        "remove_bead": "remove_beads",
        "prune_bead": "remove_beads",
        "prune_beads": "remove_beads",
        "source_removed": "remove_source",
        "remove_source_beads": "remove_source",
        "delete_source": "remove_source",
        "delete_source_beads": "remove_source",
        "inspect": "inspect_state",
        "state": "inspect_state",
        "inspect_memory": "inspect_state",
        "request_approval": "request_memory_approval",
        "approve": "approve_memory",
        "reject": "reject_memory",
        "confirm": "confirm_memory",
        "flush": "flush_session",
        "session_flush": "flush_session",
        "start_session": "session_start",
        "enqueue_async_job": "enqueue_job",
        "run_async_jobs": "run_jobs",
        "run_association_judge": "association_run",
        "run_association_coverage": "association_run",
        "apply_associations": "apply_association_proposals",
        "list_dreamer": "list_dreamer_candidates",
        "decide_dreamer": "decide_dreamer_candidate",
        "submit_entity_merge": "submit_entity_merge_proposal",
        "decide_proposal": "apply_reviewed_proposal",
    }
    return aliases.get(a, a)


def _preview(action: str, *, root: str, targets: dict[str, Any], decision: dict[str, Any], authority: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "contract": MAINTAIN_CONTRACT,
        "action": action,
        "status": "preview",
        "applied": False,
        "root": root,
        "targets": targets,
        "decision": decision,
        "authority": authority,
    }


def remove_beads(
    *,
    root: str = ".",
    bead_ids: list[str],
    reason: str,
    actor: str = "",
    authority: dict[str, Any] | None = None,
    dry_run: bool = True,
    apply: bool = False,
    source: dict[str, Any] | None = None,
    idempotency_key: str = "",
) -> dict[str, Any]:
    from core_memory.persistence.store import MemoryStore

    return MemoryStore(root=root).remove_beads(
        list(bead_ids or []),
        reason=reason,
        actor=actor,
        authority=dict(authority or {}),
        dry_run=bool(dry_run),
        apply=bool(apply),
        source=dict(source or {}),
        idempotency_key=idempotency_key,
    )


def remove_bead(
    *,
    root: str = ".",
    bead_id: str,
    reason: str,
    actor: str = "",
    authority: dict[str, Any] | None = None,
    dry_run: bool = True,
    apply: bool = False,
    idempotency_key: str = "",
) -> dict[str, Any]:
    return remove_beads(
        root=root,
        bead_ids=[bead_id],
        reason=reason,
        actor=actor,
        authority=authority,
        dry_run=dry_run,
        apply=apply,
        idempotency_key=idempotency_key,
    )


def remove_source(
    *,
    root: str = ".",
    source: dict[str, Any],
    reason: str = "source removed",
    actor: str = "",
    authority: dict[str, Any] | None = None,
    dry_run: bool = True,
    apply: bool = False,
    idempotency_key: str = "",
    limit: int = 1000,
) -> dict[str, Any]:
    from core_memory.persistence.store import MemoryStore

    return MemoryStore(root=root).remove_source(
        dict(source or {}),
        reason=reason,
        actor=actor,
        authority=dict(authority or {}),
        dry_run=bool(dry_run),
        apply=bool(apply),
        idempotency_key=idempotency_key,
        limit=max(1, int(limit)),
    )


def maintain(
    *,
    root: str = ".",
    action: str,
    scope: dict[str, Any] | None = None,
    targets: dict[str, Any] | None = None,
    proposal: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
    authority: dict[str, Any] | None = None,
    dry_run: bool = True,
    apply: bool = False,
    idempotency_key: str = "",
) -> dict[str, Any]:
    action_n = _normalize_action(action)
    scope_d = dict(scope or {})
    targets_d = dict(targets or {})
    proposal_d = dict(proposal or {})
    decision_d = dict(decision or {})
    authority_d = dict(authority or {})
    root_final = _clean_str(root or scope_d.get("root")) or "."
    apply_requested = bool(apply) and not bool(dry_run)

    if not action_n:
        return {"ok": False, "contract": MAINTAIN_CONTRACT, "error": "maintain_action_required"}

    if action_n == "inspect_state":
        from core_memory.integrations.api import inspect_state

        out = inspect_state(
            root=root_final,
            session_id=_clean_str(scope_d.get("session_id")) or None,
            as_of=_clean_str(scope_d.get("as_of")) or None,
        )
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "inspect_bead":
        from core_memory.integrations.api import inspect_bead

        bead_id = _clean_str(targets_d.get("bead_id") or (targets_d.get("bead_ids") or [""])[0])
        bead = inspect_bead(root=root_final, bead_id=bead_id)
        if bead is None:
            return {"ok": False, "contract": MAINTAIN_CONTRACT, "action": action_n, "error": "bead_not_found", "bead_id": bead_id}
        return {"ok": True, "contract": MAINTAIN_CONTRACT, "action": action_n, "bead": bead}

    if action_n == "list_pending_approvals":
        from core_memory import list_pending_approvals

        out = dict(list_pending_approvals(root=root_final, limit=int(targets_d.get("limit") or 100)))
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "remove_beads":
        bead_ids = list(targets_d.get("bead_ids") or [])
        if targets_d.get("bead_id"):
            bead_ids.append(_clean_str(targets_d.get("bead_id")))
        return remove_beads(
            root=root_final,
            bead_ids=bead_ids,
            reason=_clean_str(decision_d.get("reason") or proposal_d.get("reason") or targets_d.get("reason")),
            actor=_clean_str(authority_d.get("actor")),
            authority=authority_d,
            dry_run=bool(dry_run),
            apply=bool(apply),
            idempotency_key=idempotency_key,
        )

    if action_n == "remove_source":
        return remove_source(
            root=root_final,
            source=dict(targets_d.get("source") or proposal_d.get("source") or {}),
            reason=_clean_str(decision_d.get("reason") or proposal_d.get("reason") or targets_d.get("reason") or "source removed"),
            actor=_clean_str(authority_d.get("actor")),
            authority=authority_d,
            dry_run=bool(dry_run),
            apply=bool(apply),
            idempotency_key=idempotency_key,
            limit=int(targets_d.get("limit") or 1000),
        )

    mutating_preview_actions = {
        "request_memory_approval",
        "approve_memory",
        "reject_memory",
        "confirm_memory",
        "flush_session",
        "session_start",
        "enqueue_job",
        "run_jobs",
        "association_run",
        "apply_association_proposals",
        "decide_dreamer_candidate",
        "submit_entity_merge_proposal",
        "apply_reviewed_proposal",
    }
    if action_n in mutating_preview_actions and not apply_requested:
        return _preview(action_n, root=root_final, targets=targets_d, decision=decision_d, authority=authority_d)

    if action_n == "request_memory_approval":
        from core_memory import request_approval

        return {**request_approval(root=root_final, bead_id=_clean_str(targets_d.get("bead_id")), requested_by=_clean_str(authority_d.get("actor")), note=_clean_str(decision_d.get("note"))), "contract": MAINTAIN_CONTRACT, "action": action_n}

    if action_n == "approve_memory":
        from core_memory import approve_bead

        return {**approve_bead(root=root_final, bead_id=_clean_str(targets_d.get("bead_id")), approver=_clean_str(authority_d.get("actor")), note=_clean_str(decision_d.get("note"))), "contract": MAINTAIN_CONTRACT, "action": action_n}

    if action_n == "reject_memory":
        from core_memory import reject_bead

        return {**reject_bead(root=root_final, bead_id=_clean_str(targets_d.get("bead_id")), approver=_clean_str(authority_d.get("actor")), reason=_clean_str(decision_d.get("reason"))), "contract": MAINTAIN_CONTRACT, "action": action_n}

    if action_n == "confirm_memory":
        from core_memory import confirm_bead

        return {**confirm_bead(root=root_final, bead_id=_clean_str(targets_d.get("bead_id")), note=_clean_str(decision_d.get("note"))), "contract": MAINTAIN_CONTRACT, "action": action_n}

    if action_n == "flush_session":
        from core_memory.runtime.engine import process_flush

        out = process_flush(
            root=root_final,
            session_id=_clean_str(scope_d.get("session_id") or targets_d.get("session_id") or "default"),
            source=_clean_str(authority_d.get("actor") or "maintain"),
            flush_tx_id=_clean_str(idempotency_key),
        )
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "session_start":
        from core_memory.runtime.engine import process_session_start

        out = process_session_start(
            root=root_final,
            session_id=_clean_str(scope_d.get("session_id") or targets_d.get("session_id") or "default"),
            source=_clean_str(authority_d.get("actor") or "maintain"),
            max_items=int(targets_d.get("max_items") or 80),
        )
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "enqueue_job":
        from core_memory.runtime.queue.jobs import enqueue_async_job

        out = enqueue_async_job(root=root_final, kind=_clean_str(targets_d.get("kind")), event=dict(proposal_d.get("event") or {}), ctx=dict(scope_d.get("ctx") or {}))
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "run_jobs":
        from core_memory.runtime.queue.jobs import run_async_jobs

        out = run_async_jobs(root=root_final, run_semantic=bool(targets_d.get("run_semantic", True)), max_compaction=int(targets_d.get("max_compaction") or 1), max_side_effects=int(targets_d.get("max_side_effects") or 2))
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "association_run":
        from core_memory.runtime.associations.coverage import enqueue_association_coverage

        out = enqueue_association_coverage(
            root=root_final,
            bead_ids=list(targets_d.get("bead_ids") or []),
            session_id=_clean_str(scope_d.get("session_id") or targets_d.get("session_id")) or None,
            trigger=_clean_str(targets_d.get("trigger") or "operator"),
            candidate_bead_ids=list(targets_d.get("candidate_bead_ids") or []),
            run_inline=bool(targets_d.get("run_inline")),
            max_candidates=int(targets_d.get("max_candidates") or 40),
        )
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "apply_association_proposals":
        from core_memory.runtime.associations.coverage import apply_association_proposals

        out = apply_association_proposals(
            root=root_final,
            associations=list(proposal_d.get("associations") or []),
            run_id=_clean_str(targets_d.get("run_id")),
            session_id=_clean_str(scope_d.get("session_id")) or None,
        )
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "list_dreamer_candidates":
        from core_memory.runtime.dreamer.candidates import list_dreamer_candidates

        out = list_dreamer_candidates(root=root_final, status=_clean_str(targets_d.get("status")) or None, limit=int(targets_d.get("limit") or 100))
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "decide_dreamer_candidate":
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate

        out = decide_dreamer_candidate(
            root=root_final,
            candidate_id=_clean_str(targets_d.get("candidate_id")),
            decision=_clean_str(decision_d.get("decision") or targets_d.get("decision")),
            reviewer=_clean_str(authority_d.get("actor")),
            notes=_clean_str(decision_d.get("notes")),
            apply=True,
        )
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "submit_entity_merge_proposal":
        from core_memory.integrations.mcp.typed_write import submit_entity_merge_proposal

        out = submit_entity_merge_proposal(root=root_final, **proposal_d)
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "apply_reviewed_proposal":
        from core_memory.integrations.mcp.typed_write import apply_reviewed_proposal

        out = apply_reviewed_proposal(root=root_final, apply=True, **decision_d)
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    return {
        "ok": False,
        "contract": MAINTAIN_CONTRACT,
        "action": action_n,
        "error": "unsupported_maintain_action",
    }


__all__ = [
    "MAINTAIN_CONTRACT",
    "maintain",
    "remove_bead",
    "remove_beads",
    "remove_source",
]
