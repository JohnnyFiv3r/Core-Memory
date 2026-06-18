from __future__ import annotations

"""Advisory Dreamer research through the semantic task runtime.

The Dreamer researcher is allowed to explain, prioritize, and qualify existing
candidate findings. It does not create canonical beads, write associations, or
apply SOUL changes.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.runtime.dreamer.candidates import _read_candidates, _write_candidates
from core_memory.runtime.semantic_tasks import SemanticTaskRequest, get_semantic_task_runtime
from core_memory.runtime.semantic_tasks.contracts import TASK_DREAMER_RESEARCH
from core_memory.runtime.semantic_tasks.verifier import verify_semantic_task_output

DREAMER_RESEARCH_CONTRACT = "memory.dreamer_research.v1"
DREAMER_RESEARCH_PROMPT_VERSION = "dreamer_research.v1"
DREAMER_RESEARCH_RUBRIC_VERSION = "dreamer_research_candidate_only.v1"
DREAMER_RESEARCH_OUTPUT_SCHEMA = DREAMER_RESEARCH_CONTRACT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_index(root: str | Path) -> dict[str, Any]:
    p = Path(root) / ".beads" / "index.json"
    if not p.exists():
        return {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _clean_text(value: Any, *, max_len: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > max_len:
        return text[: max(0, max_len - 3)].rstrip() + "..."
    return text


def _as_string_list(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _clean_text(item, max_len=160)
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _candidate_snapshot(candidate: dict[str, Any]) -> dict[str, Any]:
    supporting = [
        str(x)
        for x in list(candidate.get("supporting_bead_ids") or [])
        if str(x or "").strip()
    ][:12]
    for key in ("source_bead_id", "target_bead_id"):
        value = str(candidate.get(key) or "").strip()
        if value and value not in supporting:
            supporting.append(value)
    return {
        "candidate_id": str(candidate.get("id") or ""),
        "status": str(candidate.get("status") or ""),
        "hypothesis_type": str(candidate.get("hypothesis_type") or ""),
        "proposal_family": str(candidate.get("proposal_family") or ""),
        "relationship": str(candidate.get("relationship_signal") or candidate.get("relationship") or ""),
        "statement": _clean_text(candidate.get("statement") or candidate.get("rationale") or "", max_len=700),
        "expected_decision_impact": _clean_text(candidate.get("expected_decision_impact") or "", max_len=500),
        "confidence": candidate.get("confidence"),
        "grounding": candidate.get("grounding"),
        "novelty": candidate.get("novelty"),
        "supporting_bead_ids": supporting[:12],
        "source_candidates": _as_string_list(candidate.get("source_candidates"), limit=12),
    }


def _candidate_context(candidates: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    pending = [
        c
        for c in candidates
        if str(c.get("id") or "").strip()
        and str(c.get("status") or "").strip().lower() in {"pending", "unreviewed"}
    ]
    pending.sort(key=lambda c: str(c.get("created_at") or ""), reverse=True)
    return [_candidate_snapshot(c) for c in pending[: max(1, int(limit))]]


def _bead_context(root: str | Path, candidates: list[dict[str, Any]], *, limit: int = 24) -> list[dict[str, Any]]:
    index = _read_index(root)
    bead_map = (index.get("beads") or {}) if isinstance(index, dict) else {}
    if not isinstance(bead_map, dict):
        return []

    wanted: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        for key in ("supporting_bead_ids", "related_bead_ids"):
            for bead_id in list(candidate.get(key) or []):
                bid = str(bead_id or "").strip()
                if bid and bid not in seen:
                    seen.add(bid)
                    wanted.append(bid)
        for key in ("source_bead_id", "target_bead_id", "goal_bead_id"):
            bid = str(candidate.get(key) or "").strip()
            if bid and bid not in seen:
                seen.add(bid)
                wanted.append(bid)
        if len(wanted) >= limit:
            break

    out: list[dict[str, Any]] = []
    for bid in wanted[:limit]:
        bead = bead_map.get(bid)
        if not isinstance(bead, dict):
            continue
        summary = bead.get("summary")
        summary_text = " ".join(str(x) for x in summary) if isinstance(summary, list) else str(summary or "")
        out.append(
            {
                "bead_id": bid,
                "type": str(bead.get("type") or ""),
                "status": str(bead.get("status") or ""),
                "title": _clean_text(bead.get("title") or "", max_len=180),
                "summary": _clean_text(summary_text or bead.get("detail") or "", max_len=500),
                "session_id": str(bead.get("session_id") or ""),
                "entities": _as_string_list(bead.get("entities"), limit=8),
                "topics": _as_string_list(bead.get("topics"), limit=8),
            }
        )
    return out


def _prompt() -> str:
    return (
        "You are the Core Memory Dreamer researcher. Review the provided Dreamer "
        "candidate queue and supporting bead summaries. Return JSON only.\n\n"
        "Authority boundary: candidate_only. You may explain, prioritize, and "
        "qualify candidates for later human/governed review. You must not claim "
        "to write memory, create goals, apply SOUL changes, or activate graph "
        "edges. If evidence is weak, say what would falsify or clarify it.\n\n"
        "Return this shape:\n"
        "{\n"
        '  "contract": "memory.dreamer_research.v1",\n'
        '  "run_id": "...",\n'
        '  "candidate_refinements": [\n'
        "    {\n"
        '      "candidate_id": "dc-...",\n'
        '      "research_note": "short review note",\n'
        '      "suggested_review_priority": "high|normal|low",\n'
        '      "confidence": 0.0,\n'
        '      "evidence_limitations": ["..."],\n'
        '      "falsifiability": "what would disconfirm or narrow this"\n'
        "    }\n"
        "  ],\n"
        '  "suggested_hypotheses": []\n'
        "}\n\n"
        "Only reference candidate_ids that appear in the input. Keep notes "
        "grounded in the supplied evidence."
    )


def _priority(value: Any) -> str:
    priority = str(value or "").strip().lower()
    if priority in {"high", "normal", "low"}:
        return priority
    if priority in {"medium", "med"}:
        return "normal"
    return "normal"


def _confidence(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return parsed


def _normalize_refinements(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("candidate_refinements")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("candidate_id") or "").strip()
        note = _clean_text(item.get("research_note") or item.get("note") or "", max_len=900)
        if not cid or not note:
            continue
        row: dict[str, Any] = {
            "candidate_id": cid,
            "research_note": note,
            "suggested_review_priority": _priority(item.get("suggested_review_priority") or item.get("priority")),
            "evidence_limitations": _as_string_list(item.get("evidence_limitations"), limit=6),
            "falsifiability": _clean_text(item.get("falsifiability") or "", max_len=500),
        }
        conf = _confidence(item.get("confidence"))
        if conf is not None:
            row["confidence"] = conf
        out.append(row)
    return out


def _semantic_task_ref(*, task_id: str, receipt_id: str) -> dict[str, str]:
    return {
        "task_type": TASK_DREAMER_RESEARCH,
        "task_id": str(task_id or ""),
        "receipt_id": str(receipt_id or ""),
        "role": "dreamer_research_refinement",
    }


def _append_unique_semantic_ref(row: dict[str, Any], ref: dict[str, str]) -> None:
    refs = [x for x in list(row.get("semantic_task_refs") or []) if isinstance(x, dict)]
    marker = (ref.get("task_type"), ref.get("task_id"), ref.get("role"))
    for existing in refs:
        if (
            str(existing.get("task_type") or ""),
            str(existing.get("task_id") or ""),
            str(existing.get("role") or ""),
        ) == marker:
            row["semantic_task_refs"] = refs
            return
    refs.append(ref)
    row["semantic_task_refs"] = refs


def _apply_candidate_refinements(
    root: str | Path,
    *,
    refinements: list[dict[str, Any]],
    task_id: str,
    receipt_id: str,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not refinements:
        return {"applied": 0, "unknown_candidate_ids": []}

    rows = _read_candidates(root)
    by_id = {str(row.get("id") or ""): row for row in rows if isinstance(row, dict)}
    unknown: list[str] = []
    applied = 0
    recorded_at = _now()
    ref = _semantic_task_ref(task_id=task_id, receipt_id=receipt_id)
    verifier_ref = (verification or {}).get("task_ref") if isinstance((verification or {}).get("task_ref"), dict) else {}
    verification_summary = {
        "status": str((verification or {}).get("status") or ""),
        "decision": str((verification or {}).get("decision") or ""),
        "warnings": list((verification or {}).get("warnings") or []),
        "blocking_errors": list((verification or {}).get("blocking_errors") or []),
        "task_id": str((verification or {}).get("task_id") or ""),
        "receipt_id": str((verification or {}).get("receipt_id") or ""),
    }

    for refinement in refinements:
        cid = str(refinement.get("candidate_id") or "").strip()
        row = by_id.get(cid)
        if row is None:
            unknown.append(cid)
            continue
        entry = {
            "source": "semantic_task_runtime",
            "task_type": TASK_DREAMER_RESEARCH,
            "task_id": str(task_id or ""),
            "receipt_id": str(receipt_id or ""),
            "recorded_at": recorded_at,
            "research_note": str(refinement.get("research_note") or ""),
            "suggested_review_priority": str(refinement.get("suggested_review_priority") or "normal"),
            "evidence_limitations": list(refinement.get("evidence_limitations") or []),
            "falsifiability": str(refinement.get("falsifiability") or ""),
            "verification": verification_summary,
        }
        if "confidence" in refinement:
            entry["confidence"] = refinement["confidence"]
        existing = [x for x in list(row.get("operator_research") or []) if isinstance(x, dict)]
        existing.append(entry)
        row["operator_research"] = existing[-5:]
        _append_unique_semantic_ref(row, ref)
        if verifier_ref:
            _append_unique_semantic_ref(row, verifier_ref)
        applied += 1

    if applied:
        _write_candidates(root, rows)
    return {"applied": applied, "unknown_candidate_ids": [x for x in unknown if x]}


def run_dreamer_research(
    root: str | Path,
    *,
    run_id: str,
    source: str = "side_effect_queue",
    subject: str = "self",
    candidate_limit: int = 12,
) -> dict[str, Any]:
    """Run advisory Dreamer research over pending candidate findings.

    This function is deliberately side-effect-limited: successful model output
    may annotate candidate rows, but it cannot create canonical graph/SOUL state.
    """

    rows = _read_candidates(root)
    candidates = _candidate_context(rows, limit=candidate_limit)
    if not candidates:
        return {
            "ok": True,
            "status": "skipped",
            "reason": "no_pending_candidates",
            "refined": 0,
        }

    payload = {
        "contract": DREAMER_RESEARCH_CONTRACT,
        "run_id": str(run_id or ""),
        "source": str(source or ""),
        "subject": str(subject or "self"),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "supporting_beads": _bead_context(root, rows, limit=24),
    }
    evidence_refs = [
        {"type": "dreamer_candidate", "candidate_id": str(c.get("candidate_id") or "")}
        for c in candidates
        if str(c.get("candidate_id") or "")
    ]

    runtime = get_semantic_task_runtime()
    result = runtime.run(
        SemanticTaskRequest(
            root=str(root),
            task_type=TASK_DREAMER_RESEARCH,
            prompt=_prompt() + "\n\nContext JSON:\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True),
            payload=payload,
            idempotency_key=f"dreamer_research:{run_id}",
            prompt_version=DREAMER_RESEARCH_PROMPT_VERSION,
            rubric_version=DREAMER_RESEARCH_RUBRIC_VERSION,
            output_schema=DREAMER_RESEARCH_OUTPUT_SCHEMA,
            max_tokens=1800,
            temperature=0.2,
            json_mode=True,
            fallback_mode="deterministic_dreamer_candidates",
            authority_boundary="candidate_only",
            evidence_refs=evidence_refs,
            metadata={
                "run_id": str(run_id or ""),
                "source": str(source or ""),
                "candidate_count": len(candidates),
                "candidate_ids": [str(c.get("candidate_id") or "") for c in candidates],
            },
        )
    )

    out: dict[str, Any] = {
        "ok": bool(result.ok),
        "status": str(result.status or ""),
        "task_id": str(result.task_id or ""),
        "receipt_id": str(result.receipt_id or ""),
        "authority_boundary": str(result.authority_boundary or "candidate_only"),
        "refined": 0,
    }
    if result.error:
        out["error"] = result.error
    if not result.ok:
        return out

    payload_out = result.output_json or {}
    if not isinstance(payload_out, dict):
        out["status"] = "succeeded_unparsed"
        return out

    verification = verify_semantic_task_output(
        root=str(root),
        source_task_type=TASK_DREAMER_RESEARCH,
        source_task_id=str(result.task_id or ""),
        source_receipt_id=str(result.receipt_id or ""),
        output_schema=DREAMER_RESEARCH_OUTPUT_SCHEMA,
        output_json=payload_out,
        authority_boundary="candidate_only",
        evidence_refs=evidence_refs,
        required_top_level_fields=["candidate_refinements"],
        policy_rubric="Dreamer research may annotate candidate findings only; it cannot create beads, activate edges, write SOUL, or omit evidence limitations for claims.",
        require_semantic_verifier=False,
        runtime=runtime,
    )
    out["verification"] = {
        "ok": bool(verification.get("ok")),
        "status": str(verification.get("status") or ""),
        "decision": str(verification.get("decision") or ""),
        "task_id": str(verification.get("task_id") or ""),
        "receipt_id": str(verification.get("receipt_id") or ""),
        "warnings": list(verification.get("warnings") or []),
        "blocking_errors": list(verification.get("blocking_errors") or []),
    }
    if not verification.get("ok"):
        out["ok"] = False
        out["status"] = "blocked_by_verifier"
        out["error"] = "dreamer_research_verifier_blocked_output"
        return out

    refinements = _normalize_refinements(payload_out)
    applied = _apply_candidate_refinements(
        root,
        refinements=refinements,
        task_id=str(result.task_id or ""),
        receipt_id=str(result.receipt_id or ""),
        verification=verification,
    )
    suggested = payload_out.get("suggested_hypotheses")
    out.update(
        {
            "contract": str(payload_out.get("contract") or DREAMER_RESEARCH_CONTRACT),
            "refined": int(applied.get("applied") or 0),
            "unknown_candidate_ids": list(applied.get("unknown_candidate_ids") or []),
            "suggested_hypotheses": len(suggested) if isinstance(suggested, list) else 0,
        }
    )
    return out


__all__ = [
    "DREAMER_RESEARCH_CONTRACT",
    "DREAMER_RESEARCH_OUTPUT_SCHEMA",
    "DREAMER_RESEARCH_PROMPT_VERSION",
    "DREAMER_RESEARCH_RUBRIC_VERSION",
    "run_dreamer_research",
]
