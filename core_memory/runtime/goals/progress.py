"""Model-authored evidence-to-goal progress proposals and reviewed backfill."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.goal_lifecycle_v2 import current_goal_status
from core_memory.persistence.io_utils import append_jsonl
from core_memory.policy.semantic_task_runtime import get_semantic_task_runtime
from core_memory.runtime.associations.coverage import (
    enqueue_goal_progress_candidate,
    judge_association_candidates,
    list_association_candidates,
)
from core_memory.runtime.queue.side_effect_queue import enqueue_side_effect_event
from core_memory.schema.semantic_tasks import TASK_GOAL_PROGRESS, SemanticTaskRequest

GOAL_PROGRESS_CONTRACT = "memory.goal_progress.v1"
GOAL_PROGRESS_BACKFILL_CONTRACT = "memory.goal_progress_backfill.v1"
GOAL_PROGRESS_STATUS_CONTRACT = "memory.goal_progress_status.v1"
GOAL_PROGRESS_SCHEMA = "core_memory.goal_progress.v1"
GOAL_PROGRESS_BACKFILL_SCHEMA = "core_memory.goal_progress_backfill.v1"
GOAL_PROGRESS_PROMPT_VERSION = "goal_progress.v1"
GOAL_PROGRESS_RUBRIC_VERSION = "goal_progress_truth.v1"
DEFAULT_EVALUATOR_VERSION = "goal_progress.v1"
ELIGIBLE_GOAL_STATES = frozenset({"candidate", "endorsed", "active"})
_INELIGIBLE_EVIDENCE_STATUSES = frozenset(
    {"deleted", "rejected", "archived", "superseded", "abandoned", "completed", "resolved"}
)
_TERMINAL_TASK_STATUSES = frozenset({"proposed", "no_link", "quarantined"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _events_dir(root: str | Path) -> Path:
    path = Path(root) / ".beads" / "events"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _task_path(root: str | Path) -> Path:
    return _events_dir(root) / "goal-progress-tasks.jsonl"


def _backfill_path(root: str | Path) -> Path:
    return _events_dir(root) / "goal-progress-backfill.jsonl"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _append_task(root: str | Path, row: dict[str, Any]) -> None:
    append_jsonl(
        _task_path(root),
        {"schema": GOAL_PROGRESS_SCHEMA, "recorded_at": _now(), **dict(row)},
    )


def _append_backfill(root: str | Path, row: dict[str, Any]) -> None:
    append_jsonl(
        _backfill_path(root),
        {"schema": GOAL_PROGRESS_BACKFILL_SCHEMA, "recorded_at": _now(), **dict(row)},
    )


def _load_index(root: str | Path) -> dict[str, Any]:
    path = Path(root) / ".beads" / "index.json"
    if not path.exists():
        return {"beads": {}, "associations": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"beads": {}, "associations": []}
    if not isinstance(data, dict):
        return {"beads": {}, "associations": []}
    data.setdefault("beads", {})
    data.setdefault("associations", [])
    return data


def _eligible_goal(bead: Any) -> bool:
    return (
        isinstance(bead, dict)
        and _clean(bead.get("type")).lower() == "goal"
        and current_goal_status(bead) in ELIGIBLE_GOAL_STATES
        and _clean(bead.get("status")).lower() not in _INELIGIBLE_EVIDENCE_STATUSES
    )


def _eligible_evidence(bead: Any) -> bool:
    if not isinstance(bead, dict) or _clean(bead.get("type")).lower() in {"", "goal"}:
        return False
    if bead.get("retrieval_eligible") is False:
        return False
    return _clean(bead.get("status")).lower() not in _INELIGIBLE_EVIDENCE_STATUSES


def _active_support_sources(index: dict[str, Any], goal_id: str) -> set[str]:
    out: set[str] = set()
    for association in index.get("associations") or []:
        if not isinstance(association, dict):
            continue
        if _clean(association.get("relationship")).lower() != "supports":
            continue
        if _clean(association.get("target_bead") or association.get("target_bead_id")) != goal_id:
            continue
        if _clean(association.get("status")).lower() not in {"", "active", "current", "linked"}:
            continue
        source_id = _clean(association.get("source_bead") or association.get("source_bead_id"))
        if source_id:
            out.add(source_id)
    return out


def _pair_rows(
    index: dict[str, Any],
    *,
    evidence_bead_ids: list[str] | None = None,
    goal_bead_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    beads = index.get("beads") or {}
    requested_evidence = {_clean(value) for value in (evidence_bead_ids or []) if _clean(value)}
    requested_goals = {_clean(value) for value in (goal_bead_ids or []) if _clean(value)}
    goals = sorted(
        bead_id
        for bead_id, bead in beads.items()
        if _eligible_goal(bead) and (not requested_goals or bead_id in requested_goals)
    )
    evidence = sorted(
        bead_id
        for bead_id, bead in beads.items()
        if _eligible_evidence(bead) and (not requested_evidence or bead_id in requested_evidence)
    )
    rows: list[dict[str, Any]] = []
    for goal_id in goals:
        support_sources = _active_support_sources(index, goal_id)
        for evidence_id in evidence:
            rows.append(
                {
                    "goal_bead_id": goal_id,
                    "evidence_bead_id": evidence_id,
                    "supports_seed": evidence_id in support_sources,
                    "cursor_key": [0 if evidence_id in support_sources else 1, goal_id, evidence_id],
                }
            )
    rows.sort(key=lambda row: tuple(row["cursor_key"]))
    return rows


def _bead_context(bead_id: str, bead: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "type",
        "title",
        "summary",
        "detail",
        "because",
        "supporting_facts",
        "state_change",
        "result",
        "goal_id",
        "goal_status",
        "success_criteria",
        "created_at",
        "observed_at",
        "effective_from",
        "effective_to",
        "source_id",
        "source_system",
        "source_event_id",
        "source_attribution",
        "evidence_refs",
    )
    return {"id": bead_id, **{key: bead[key] for key in keys if bead.get(key) not in (None, "", [], {})}}


def _idempotency_key(goal_id: str, evidence_id: str, evaluator_version: str) -> str:
    return f"goal_advance:{goal_id}:{evidence_id}:{evaluator_version}"


def _candidate_id(idempotency_key: str) -> str:
    return "goal-cand-" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]


def _latest_task_by_key(root: str | Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(_task_path(root)):
        key = _clean(row.get("idempotency_key"))
        if key:
            latest[key] = row
    return latest


def _prompt(payload: dict[str, Any]) -> str:
    return (
        "You are Core Memory's semantic goal-progress producer. Decide whether the supplied "
        "evidence bead is an observed result or state change that demonstrates progress toward "
        "the supplied non-terminal Goal Bead. Relevance, intent, a mention, or a supports edge "
        "is not progress. Return only JSON matching memory.goal_progress.v1. Use decision "
        "no_link when progress is not evidenced. When progress is evidenced, use decision "
        "propose and include the exact source_bead_id, target_goal_bead_id, relationship "
        "advances_goal, rationale, evidence_refs, provenance_refs, temporal_bounds, confidence, "
        "evaluator_version, and exact idempotency_key from the request. This task proposes only; "
        "a separate association judge decides graph truth.\n\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _evaluate_pair(
    root: str | Path,
    *,
    evidence_id: str,
    goal_id: str,
    supports_seed: bool,
    evaluator_version: str,
    run_id: str,
    trigger: str,
) -> dict[str, Any]:
    index = _load_index(root)
    beads = index.get("beads") or {}
    evidence = beads.get(evidence_id)
    goal = beads.get(goal_id)
    idem = _idempotency_key(goal_id, evidence_id, evaluator_version)
    latest = _latest_task_by_key(root).get(idem)
    existing_candidate = next(
        (
            row
            for row in list_association_candidates(root, limit=100000).get("results") or []
            if _clean(row.get("candidate_id")) == _candidate_id(idem)
        ),
        None,
    )
    if existing_candidate is not None or _clean((latest or {}).get("status")) in _TERMINAL_TASK_STATUSES:
        return {
            "ok": True,
            "status": _clean((existing_candidate or {}).get("status")) or _clean((latest or {}).get("status")),
            "deduped": True,
            "candidate_id": _clean((existing_candidate or {}).get("candidate_id")),
            "idempotency_key": idem,
        }
    if not _eligible_evidence(evidence) or not _eligible_goal(goal):
        _append_task(
            root,
            {
                "contract": GOAL_PROGRESS_CONTRACT,
                "run_id": run_id,
                "trigger": trigger,
                "status": "skipped_ineligible",
                "source_bead_id": evidence_id,
                "target_goal_bead_id": goal_id,
                "evaluator_version": evaluator_version,
                "idempotency_key": idem,
            },
        )
        return {"ok": True, "status": "skipped_ineligible", "idempotency_key": idem}

    request_payload = {
        "contract": GOAL_PROGRESS_CONTRACT,
        "source_bead_id": evidence_id,
        "target_goal_bead_id": goal_id,
        "relationship": "advances_goal",
        "supports_seed": bool(supports_seed),
        "evaluator_version": evaluator_version,
        "idempotency_key": idem,
        "evidence": _bead_context(evidence_id, evidence),
        "goal": _bead_context(goal_id, goal),
    }
    result = get_semantic_task_runtime().run(
        SemanticTaskRequest(
            task_type=TASK_GOAL_PROGRESS,
            root=str(root),
            task_id="goal-progress-" + hashlib.sha256(idem.encode("utf-8")).hexdigest()[:16],
            idempotency_key=idem,
            prompt=_prompt(request_payload),
            payload=request_payload,
            prompt_version=GOAL_PROGRESS_PROMPT_VERSION,
            rubric_version=GOAL_PROGRESS_RUBRIC_VERSION,
            output_schema=GOAL_PROGRESS_CONTRACT,
            model_tier="standard",
            max_tokens=1000,
            temperature=0,
            json_mode=True,
            fallback_mode="pending_semantic_author",
            authority_boundary="semantic_author",
            evidence_refs=[{"bead_id": evidence_id}, {"bead_id": goal_id}],
            metadata={"policy": "goal_progress", "run_id": run_id, "trigger": trigger},
        )
    )
    if not result.ok:
        status = "unavailable" if _clean(result.status) == "unavailable" else "failed"
        _append_task(
            root,
            {
                "contract": GOAL_PROGRESS_CONTRACT,
                "run_id": run_id,
                "trigger": trigger,
                "status": status,
                "source_bead_id": evidence_id,
                "target_goal_bead_id": goal_id,
                "evaluator_version": evaluator_version,
                "idempotency_key": idem,
                "semantic_task_id": _clean(result.task_id),
                "semantic_receipt_id": _clean(result.receipt_id),
                "error": _clean(result.error) or _clean(result.status),
            },
        )
        return {"ok": False, "status": status, "idempotency_key": idem, "error": _clean(result.error)}

    output = dict(result.output_json or {})
    proposal = dict(output.get("proposal") or {}) if isinstance(output.get("proposal"), dict) else output
    decision = _clean(output.get("decision") or proposal.get("decision")).lower()
    reason = _clean(
        output.get("reason_text")
        or output.get("rationale")
        or proposal.get("reason_text")
        or proposal.get("rationale")
    )
    if decision in {"no_link", "reject", "none"}:
        _append_task(
            root,
            {
                "contract": GOAL_PROGRESS_CONTRACT,
                "run_id": run_id,
                "trigger": trigger,
                "status": "no_link",
                "source_bead_id": evidence_id,
                "target_goal_bead_id": goal_id,
                "evaluator_version": evaluator_version,
                "idempotency_key": idem,
                "semantic_task_id": _clean(result.task_id),
                "semantic_receipt_id": _clean(result.receipt_id),
                "reason_text": reason,
            },
        )
        return {"ok": True, "status": "no_link", "idempotency_key": idem}

    validation_errors: list[str] = []
    if decision != "propose":
        validation_errors.append("invalid_decision")
    if _clean(proposal.get("source_bead_id") or proposal.get("source_bead")) != evidence_id:
        validation_errors.append("source_bead_mismatch")
    proposal_target = _clean(
        proposal.get("target_goal_bead_id")
        or proposal.get("target_bead_id")
        or proposal.get("target_bead")
    )
    if proposal_target != goal_id:
        validation_errors.append("target_goal_bead_mismatch")
    if _clean(proposal.get("relationship")).lower() != "advances_goal":
        validation_errors.append("relationship_mismatch")
    if _clean(proposal.get("evaluator_version")) != evaluator_version:
        validation_errors.append("evaluator_version_mismatch")
    if _clean(proposal.get("idempotency_key")) != idem:
        validation_errors.append("idempotency_key_mismatch")
    candidate = enqueue_goal_progress_candidate(
        root,
        source_bead_id=evidence_id,
        target_goal_bead_id=goal_id,
        rationale=reason,
        confidence=proposal.get("confidence"),
        evidence_refs=list(proposal.get("evidence_refs") or []),
        provenance_refs=list(proposal.get("provenance_refs") or []),
        temporal_bounds=proposal.get("temporal_bounds"),
        evaluator_version=evaluator_version,
        idempotency_key=idem,
        run_id=run_id,
        trigger=trigger,
    ) if not validation_errors else {"ok": False, "validation_errors": validation_errors}
    if not candidate.get("ok"):
        errors = list(candidate.get("validation_errors") or [candidate.get("error")])
        _append_task(
            root,
            {
                "contract": GOAL_PROGRESS_CONTRACT,
                "run_id": run_id,
                "trigger": trigger,
                "status": "quarantined",
                "source_bead_id": evidence_id,
                "target_goal_bead_id": goal_id,
                "evaluator_version": evaluator_version,
                "idempotency_key": idem,
                "semantic_task_id": _clean(result.task_id),
                "semantic_receipt_id": _clean(result.receipt_id),
                "validation_errors": errors,
            },
        )
        return {"ok": True, "status": "quarantined", "idempotency_key": idem, "validation_errors": errors}

    _append_task(
        root,
        {
            "contract": GOAL_PROGRESS_CONTRACT,
            "run_id": run_id,
            "trigger": trigger,
            "status": "proposed",
            "source_bead_id": evidence_id,
            "target_goal_bead_id": goal_id,
            "candidate_id": _clean(candidate.get("candidate_id")),
            "evaluator_version": evaluator_version,
            "idempotency_key": idem,
            "semantic_task_id": _clean(result.task_id),
            "semantic_receipt_id": _clean(result.receipt_id),
        },
    )
    return {
        "ok": True,
        "status": "pending_judge",
        "deduped": bool(candidate.get("deduped")),
        "candidate_id": _clean(candidate.get("candidate_id")),
        "idempotency_key": idem,
    }


def run_goal_progress_tasks(
    root: str | Path,
    *,
    evidence_bead_ids: list[str] | None = None,
    goal_bead_ids: list[str] | None = None,
    trigger: str = "operator",
    evaluator_version: str = DEFAULT_EVALUATOR_VERSION,
    max_pairs: int = 40,
    pair_rows: list[dict[str, Any]] | None = None,
    judge: bool = True,
) -> dict[str, Any]:
    index = _load_index(root)
    selected_pairs = (
        pair_rows
        if pair_rows is not None
        else _pair_rows(
            index,
            evidence_bead_ids=evidence_bead_ids,
            goal_bead_ids=goal_bead_ids,
        )
    )
    pairs = list(selected_pairs)[: max(1, int(max_pairs))]
    run_id = f"gprun-{uuid.uuid4().hex[:12]}"
    results: list[dict[str, Any]] = []
    candidate_ids: list[str] = []
    for pair in pairs:
        row = _evaluate_pair(
            root,
            evidence_id=_clean(pair.get("evidence_bead_id")),
            goal_id=_clean(pair.get("goal_bead_id")),
            supports_seed=bool(pair.get("supports_seed")),
            evaluator_version=_clean(evaluator_version) or DEFAULT_EVALUATOR_VERSION,
            run_id=run_id,
            trigger=_clean(trigger) or "operator",
        )
        results.append(row)
        candidate_id = _clean(row.get("candidate_id"))
        if candidate_id and _clean(row.get("status")) in {"pending_judge", "judge_failed"}:
            candidate_ids.append(candidate_id)

    judge_result: dict[str, Any] = {"ok": True, "status": "skipped", "counts": {"candidates": 0}}
    if judge and candidate_ids:
        judge_result = judge_association_candidates(
            root,
            candidate_ids=list(dict.fromkeys(candidate_ids)),
            run_id=run_id,
            trigger="goal_progress",
        )
    counts = {
        "scanned": len(pairs),
        "proposed": sum(
            1
            for row in results
            if _clean(row.get("status")) == "pending_judge" and not row.get("deduped")
        ),
        "no_link": sum(1 for row in results if _clean(row.get("status")) == "no_link"),
        "deduped": sum(1 for row in results if bool(row.get("deduped"))),
        "quarantined": sum(1 for row in results if _clean(row.get("status")) == "quarantined")
        + int((judge_result.get("counts") or {}).get("quarantined") or 0),
        "failed": sum(1 for row in results if not bool(row.get("ok")))
        + int((judge_result.get("counts") or {}).get("failed") or 0),
        "accepted": sum(1 for row in results if _clean(row.get("status")) == "linked")
        + int((judge_result.get("counts") or {}).get("accepted") or 0),
        "rejected": sum(
            1 for row in results if _clean(row.get("status")) in {"no_supported_links", "rejected"}
        ) + int((judge_result.get("counts") or {}).get("rejected") or 0),
    }
    ok = all(bool(row.get("ok")) for row in results) and bool(judge_result.get("ok"))
    judge_status = _clean(judge_result.get("status"))
    status = "completed" if ok else judge_status or "failed"
    if ok and judge_status in {"pending_judge", "judge_failed", "quarantined"}:
        status = judge_status
    return {
        "ok": ok,
        "contract": GOAL_PROGRESS_CONTRACT,
        "run_id": run_id,
        "status": status,
        "trigger": _clean(trigger) or "operator",
        "evaluator_version": _clean(evaluator_version) or DEFAULT_EVALUATOR_VERSION,
        "candidate_ids": list(dict.fromkeys(candidate_ids)),
        "counts": counts,
        "judge": judge_result,
        "results": results,
    }


def run_goal_progress_for_beads(
    root: str | Path,
    *,
    bead_ids: list[str],
    trigger: str = "bead_committed",
    evaluator_version: str = DEFAULT_EVALUATOR_VERSION,
    max_pairs: int = 40,
) -> dict[str, Any]:
    """Evaluate every goal-progress pair touched by the supplied bead set."""

    index = _load_index(root)
    pairs = _pairs_for_changed_beads(index, bead_ids)
    return run_goal_progress_tasks(
        root,
        trigger=trigger,
        evaluator_version=evaluator_version,
        max_pairs=max_pairs,
        pair_rows=pairs,
        judge=True,
    )


def _pairs_for_changed_beads(
    index: dict[str, Any],
    bead_ids: list[str],
) -> list[dict[str, Any]]:
    beads = index.get("beads") or {}
    changed_ids = list(dict.fromkeys(_clean(value) for value in bead_ids if _clean(value)))
    evidence_ids = [bead_id for bead_id in changed_ids if _eligible_evidence(beads.get(bead_id))]
    goal_ids = [bead_id for bead_id in changed_ids if _eligible_goal(beads.get(bead_id))]
    pairs: list[dict[str, Any]] = []
    if evidence_ids:
        pairs.extend(_pair_rows(index, evidence_bead_ids=evidence_ids))
    if goal_ids:
        pairs.extend(_pair_rows(index, goal_bead_ids=goal_ids))
    unique_pairs = {
        (_clean(row.get("goal_bead_id")), _clean(row.get("evidence_bead_id"))): row
        for row in pairs
    }
    return sorted(unique_pairs.values(), key=lambda row: tuple(row["cursor_key"]))


def _encode_cursor(key: list[Any]) -> str:
    raw = json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[int, str, str] | None:
    text = _clean(cursor)
    if not text:
        return None
    try:
        padded = text + "=" * (-len(text) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if isinstance(value, list) and len(value) == 3:
            return (int(value[0]), _clean(value[1]), _clean(value[2]))
    except Exception:
        return None
    return None


def backfill_goal_progress(
    root: str | Path,
    *,
    cursor: str = "",
    limit: int = 25,
    evaluator_version: str = DEFAULT_EVALUATOR_VERSION,
) -> dict[str, Any]:
    index = _load_index(root)
    all_pairs = _pair_rows(index)
    cursor_key = _decode_cursor(cursor)
    if _clean(cursor) and cursor_key is None:
        return {"ok": False, "contract": GOAL_PROGRESS_BACKFILL_CONTRACT, "error": "invalid_cursor"}
    remaining = [row for row in all_pairs if cursor_key is None or tuple(row["cursor_key"]) > cursor_key]
    page = remaining[: max(1, min(500, int(limit)))]
    result = run_goal_progress_tasks(
        root,
        trigger="backfill",
        evaluator_version=evaluator_version,
        max_pairs=max(1, len(page)),
        pair_rows=page,
        judge=True,
    ) if page else {
        "ok": True,
        "run_id": f"gprun-{uuid.uuid4().hex[:12]}",
        "counts": {"scanned": 0, "proposed": 0, "accepted": 0, "rejected": 0, "quarantined": 0, "failed": 0},
    }
    has_more = len(remaining) > len(page)
    next_cursor = _encode_cursor(page[-1]["cursor_key"]) if page and has_more and result.get("ok") else ""
    status = "in_progress" if next_cursor else ("completed" if result.get("ok") else "failed")
    receipt = {
        "ok": bool(result.get("ok")),
        "contract": GOAL_PROGRESS_BACKFILL_CONTRACT,
        "run_id": _clean(result.get("run_id")),
        "status": status,
        "cursor": _clean(cursor),
        "next_cursor": next_cursor,
        "evaluator_version": _clean(evaluator_version) or DEFAULT_EVALUATOR_VERSION,
        "counts": dict(result.get("counts") or {}),
        "total_pairs": len(all_pairs),
    }
    _append_backfill(root, receipt)
    return receipt


def goal_progress_status(root: str | Path) -> dict[str, Any]:
    index = _load_index(root)
    backfills = _read_jsonl(_backfill_path(root))
    latest = dict(backfills[-1]) if backfills else {}
    goals = [bead_id for bead_id, bead in (index.get("beads") or {}).items() if _eligible_goal(bead)]
    terminal = _clean(latest.get("status")) == "completed"
    return {
        "ok": True,
        "contract": GOAL_PROGRESS_STATUS_CONTRACT,
        "live_producer_active": True,
        "eligible_goal_count": len(goals),
        "backfill_required": bool(goals) and not terminal,
        "backfill_terminal": terminal,
        "latest_backfill": latest or None,
    }


def enqueue_goal_progress_event(
    root: str | Path,
    *,
    evidence_bead_ids: list[str] | None = None,
    goal_bead_ids: list[str] | None = None,
    trigger: str,
    evaluator_version: str = DEFAULT_EVALUATOR_VERSION,
    max_pairs: int = 40,
    idempotency_key: str = "",
) -> dict[str, Any]:
    payload = {
        "mode": "produce",
        "evidence_bead_ids": list(evidence_bead_ids or []),
        "goal_bead_ids": list(goal_bead_ids or []),
        "trigger": _clean(trigger) or "operator",
        "evaluator_version": _clean(evaluator_version) or DEFAULT_EVALUATOR_VERSION,
        "max_pairs": max(1, int(max_pairs)),
    }
    return enqueue_side_effect_event(
        root=root,
        kind="goal-progress",
        payload=payload,
        idempotency_key=_clean(idempotency_key) or None,
    )


def enqueue_goal_progress_for_beads(
    root: str | Path,
    *,
    bead_ids: list[str],
    trigger: str = "bead_committed",
    evaluator_version: str = DEFAULT_EVALUATOR_VERSION,
    max_pairs: int = 40,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Queue live production only when the changed beads touch eligible pairs."""

    index = _load_index(root)
    beads = index.get("beads") or {}
    changed_ids = list(dict.fromkeys(_clean(value) for value in bead_ids if _clean(value)))
    has_evidence = any(_eligible_evidence(beads.get(bead_id)) for bead_id in changed_ids)
    has_goal = any(_eligible_goal(beads.get(bead_id)) for bead_id in changed_ids)
    all_evidence = any(_eligible_evidence(bead) for bead in beads.values())
    all_goals = any(_eligible_goal(bead) for bead in beads.values())
    if not ((has_evidence and all_goals) or (has_goal and all_evidence)):
        return {"ok": True, "scheduled": False, "reason": "no_eligible_goal_progress_pairs"}
    return enqueue_side_effect_event(
        root=root,
        kind="goal-progress",
        payload={
            "mode": "committed",
            "bead_ids": changed_ids,
            "trigger": _clean(trigger) or "bead_committed",
            "evaluator_version": _clean(evaluator_version) or DEFAULT_EVALUATOR_VERSION,
            "max_pairs": max(1, int(max_pairs)),
        },
        idempotency_key=_clean(idempotency_key) or None,
    )


def enqueue_goal_progress_judge(
    root: str | Path,
    *,
    candidate_ids: list[str],
    run_id: str = "",
    trigger: str = "goal_progress",
) -> dict[str, Any]:
    ids = sorted({_clean(value) for value in candidate_ids if _clean(value)})
    key = "goal-progress-judge:" + hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()[:20]
    return enqueue_side_effect_event(
        root=root,
        kind="goal-progress",
        payload={"mode": "judge", "candidate_ids": ids, "run_id": _clean(run_id), "trigger": trigger},
        idempotency_key=key,
    )


def enqueue_goal_progress_backfill(
    root: str | Path,
    *,
    cursor: str = "",
    limit: int = 25,
    evaluator_version: str = DEFAULT_EVALUATOR_VERSION,
) -> dict[str, Any]:
    evaluator = _clean(evaluator_version) or DEFAULT_EVALUATOR_VERSION
    key = f"goal-progress-backfill:{evaluator}:{_clean(cursor) or 'start'}"
    return enqueue_side_effect_event(
        root=root,
        kind="goal-progress",
        payload={
            "mode": "backfill",
            "cursor": _clean(cursor),
            "limit": max(1, min(500, int(limit))),
            "evaluator_version": evaluator,
        },
        idempotency_key=key,
    )


def _process_goal_progress_pair_page(
    root: str | Path,
    *,
    pairs: list[dict[str, Any]],
    payload: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    cursor = _clean(payload.get("cursor"))
    cursor_key = _decode_cursor(cursor)
    if cursor and cursor_key is None:
        return {"ok": False, "kind": "goal-progress", "error": "invalid_cursor"}
    remaining = [row for row in pairs if cursor_key is None or tuple(row["cursor_key"]) > cursor_key]
    limit = max(1, min(500, int(payload.get("max_pairs") or 40)))
    page = remaining[:limit]
    result = run_goal_progress_tasks(
        root,
        trigger=_clean(payload.get("trigger")) or "operator",
        evaluator_version=_clean(payload.get("evaluator_version")) or DEFAULT_EVALUATOR_VERSION,
        max_pairs=limit,
        pair_rows=page,
        judge=True,
    )
    next_cursor = ""
    continuation = None
    if result.get("ok") and len(remaining) > len(page) and page:
        next_cursor = _encode_cursor(page[-1]["cursor_key"])
        follow_up = {**payload, "mode": mode, "cursor": next_cursor, "max_pairs": limit}
        identity = json.dumps(
            {
                "mode": mode,
                "bead_ids": sorted(payload.get("bead_ids") or []),
                "evidence_bead_ids": sorted(payload.get("evidence_bead_ids") or []),
                "goal_bead_ids": sorted(payload.get("goal_bead_ids") or []),
                "evaluator_version": _clean(payload.get("evaluator_version")),
                "cursor": next_cursor,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        continuation = enqueue_side_effect_event(
            root=root,
            kind="goal-progress",
            payload=follow_up,
            idempotency_key="goal-progress:continue:"
            + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        )
    ok = bool(result.get("ok")) and (
        continuation is None or bool(continuation.get("ok"))
    )
    return {
        "ok": ok,
        "kind": "goal-progress",
        "result": result,
        "next_cursor": next_cursor,
        "continuation": continuation,
        "error": None if ok else (
            result.get("error") or (continuation or {}).get("error")
        ),
    }


def process_goal_progress_event(root: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    mode = _clean(payload.get("mode")).lower() or "produce"
    if mode == "committed":
        return _process_goal_progress_pair_page(
            root,
            pairs=_pairs_for_changed_beads(
                _load_index(root),
                [_clean(value) for value in payload.get("bead_ids") or []],
            ),
            payload=payload,
            mode=mode,
        )
    if mode == "judge":
        return judge_association_candidates(
            root,
            candidate_ids=[_clean(value) for value in payload.get("candidate_ids") or []],
            run_id=_clean(payload.get("run_id")),
            trigger=_clean(payload.get("trigger")) or "goal_progress",
        )
    if mode == "backfill":
        result = backfill_goal_progress(
            root,
            cursor=_clean(payload.get("cursor")),
            limit=max(1, int(payload.get("limit") or 25)),
            evaluator_version=_clean(payload.get("evaluator_version")) or DEFAULT_EVALUATOR_VERSION,
        )
        next_cursor = _clean(result.get("next_cursor"))
        if result.get("ok") and next_cursor:
            queued = enqueue_goal_progress_backfill(
                root,
                cursor=next_cursor,
                limit=max(1, int(payload.get("limit") or 25)),
                evaluator_version=_clean(payload.get("evaluator_version")) or DEFAULT_EVALUATOR_VERSION,
            )
            if not queued.get("ok"):
                return {"ok": False, "kind": "goal-progress", "result": result, "error": queued.get("error")}
        return {"ok": bool(result.get("ok")), "kind": "goal-progress", "result": result}
    pairs = _pair_rows(
        _load_index(root),
        evidence_bead_ids=[_clean(value) for value in payload.get("evidence_bead_ids") or []],
        goal_bead_ids=[_clean(value) for value in payload.get("goal_bead_ids") or []],
    )
    return _process_goal_progress_pair_page(root, pairs=pairs, payload=payload, mode=mode)


__all__ = [
    "DEFAULT_EVALUATOR_VERSION",
    "ELIGIBLE_GOAL_STATES",
    "GOAL_PROGRESS_BACKFILL_CONTRACT",
    "GOAL_PROGRESS_CONTRACT",
    "backfill_goal_progress",
    "enqueue_goal_progress_backfill",
    "enqueue_goal_progress_event",
    "enqueue_goal_progress_for_beads",
    "enqueue_goal_progress_judge",
    "goal_progress_status",
    "process_goal_progress_event",
    "run_goal_progress_for_beads",
    "run_goal_progress_tasks",
]
