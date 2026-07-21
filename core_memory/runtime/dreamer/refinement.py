"""Semantic refinement of pending Dreamer candidates (titles + statements).

Convergence and goal discovery are deterministic detectors: their candidate
rows carry templated statements built from structure ("N continuity threads
converge across M shared beads…", "Repeated behavior involving 'token'…").
That is the correct grounded baseline, but it is not something a human can
read as a storyline name or a goal. This module is the clearly-marked LLM
step the detectors defer to: it rewrites pending ``narrative_candidate`` and
``goal_candidate`` rows into a short human title plus a grounded statement,
before review.

Authority boundary: candidate_only. Refinement may retitle and restate a
pending candidate; it never changes what the candidate is grounded in
(supporting worldlines/beads are copied through untouched), never decides,
and never writes graph truth. The original templated statement is preserved
on the row as ``statement_template`` so the deterministic derivation stays
auditable. On any runtime failure the rows are left exactly as the
detectors wrote them — refinement is strictly additive and fail-open.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.policy.semantic_task_runtime import get_semantic_task_runtime
from core_memory.runtime.dreamer.candidates import _read_candidates, _write_candidates
from core_memory.schema.semantic_tasks import (
    MODEL_TIER_STANDARD,
    SemanticTaskRequest,
    TASK_DREAMER_RESEARCH,
)

CANDIDATE_REFINEMENT_CONTRACT = "memory.dreamer_candidate_refinement.v1"
CANDIDATE_REFINEMENT_PROMPT_VERSION = "dreamer_candidate_refinement.v1"
CANDIDATE_REFINEMENT_RUBRIC_VERSION = "candidate_naming_grounded.v1"

_REFINABLE_TYPES = {"narrative_candidate", "goal_candidate"}
_MIN_TITLE_CHARS = 8
_MAX_TITLE_CHARS = 160
_MIN_STATEMENT_CHARS = 20
_MAX_STATEMENT_CHARS = 900


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_index(root: str | Path) -> dict[str, Any]:
    path = Path(root) / ".beads" / "index.json"
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _clean_text(value: Any, *, max_len: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > max_len:
        return text[: max(0, max_len - 1)].rstrip() + "…"
    return text


def _bead_digest(bead: dict[str, Any]) -> dict[str, Any]:
    summary = bead.get("summary")
    summary_text = " ".join(str(x) for x in summary) if isinstance(summary, list) else str(summary or "")
    return {
        "type": str(bead.get("type") or ""),
        "title": _clean_text(bead.get("title"), max_len=160),
        "summary": _clean_text(summary_text or bead.get("detail"), max_len=320),
    }


def _candidate_snapshot(candidate: dict[str, Any], beads: dict[str, Any]) -> dict[str, Any]:
    supporting = [str(x) for x in list(candidate.get("supporting_bead_ids") or []) if str(x).strip()]
    digests = []
    for bid in supporting[:8]:
        bead = beads.get(bid)
        if isinstance(bead, dict):
            digests.append(_bead_digest(bead))
    snapshot: dict[str, Any] = {
        "candidate_id": str(candidate.get("id") or ""),
        "hypothesis_type": str(candidate.get("hypothesis_type") or ""),
        "statement_template": _clean_text(candidate.get("statement") or candidate.get("rationale"), max_len=500),
        "supporting_beads": digests,
    }
    if str(candidate.get("hypothesis_type") or "") == "narrative_candidate":
        snapshot["worldline_labels"] = [
            _clean_text(x, max_len=120) for x in list(candidate.get("worldline_labels") or [])[:8]
        ]
        snapshot["worldline_kinds"] = [str(x) for x in list(candidate.get("kinds") or [])[:4]]
    if str(candidate.get("hypothesis_type") or "") == "goal_candidate":
        snapshot["goal_theme"] = _clean_text(candidate.get("goal_theme"), max_len=120)
        snapshot["occurrence_count"] = candidate.get("occurrence_count")
        snapshot["session_count"] = candidate.get("session_count")
    return snapshot


def _prompt() -> str:
    return (
        "You are the Core Memory storyline and goal naming refiner. Each input "
        "candidate is a grounded, deterministic detection that currently carries "
        "only a templated statement. Rewrite each into something a person would "
        "recognize in a product UI.\n\n"
        "For every candidate return:\n"
        "- title: a specific, concrete name (8-120 chars). Name what the thread "
        "or goal is actually about using the supporting bead content — never "
        "generic filler like 'Convergence of threads', 'Latent goal', or a bare "
        "entity list.\n"
        "- statement: 1-3 sentences saying what is happening across these beads "
        "and why it matters (narratives), or what the recurring intention is and "
        "what pursuing it would look like (goals). Ground every claim in the "
        "supplied bead titles/summaries; never invent people, systems, numbers, "
        "or events that are not in the input.\n\n"
        "Authority boundary: candidate_only. You are refining wording for later "
        "human review; you cannot decide, write memory, or create goals.\n\n"
        "Return JSON only:\n"
        "{\n"
        '  "contract": "memory.dreamer_candidate_refinement.v1",\n'
        '  "refinements": [\n'
        '    {"candidate_id": "dc-...", "title": "...", "statement": "..."}\n'
        "  ]\n"
        "}\n"
        "Only reference candidate_ids present in the input."
    )


def _valid_refinement(row: Any, known_ids: set[str]) -> dict[str, str] | None:
    if not isinstance(row, dict):
        return None
    cid = str(row.get("candidate_id") or "").strip()
    title = _clean_text(row.get("title"), max_len=_MAX_TITLE_CHARS)
    statement = _clean_text(row.get("statement"), max_len=_MAX_STATEMENT_CHARS)
    if cid not in known_ids:
        return None
    if len(title) < _MIN_TITLE_CHARS or len(statement) < _MIN_STATEMENT_CHARS:
        return None
    return {"candidate_id": cid, "title": title, "statement": statement}


def refine_pending_candidates(
    root: str | Path,
    *,
    run_id: str,
    source: str = "side_effect_queue",
    limit: int = 12,
) -> dict[str, Any]:
    """Retitle/restate pending narrative and goal candidates via one semantic task.

    Fail-open: when the semantic runtime is unavailable or returns an invalid
    payload, every candidate row is left untouched and the templated statements
    remain in force.
    """
    rows = _read_candidates(root)
    pending = [
        r
        for r in rows
        if isinstance(r, dict)
        and str(r.get("hypothesis_type") or "") in _REFINABLE_TYPES
        and str(r.get("status") or "").strip().lower() == "pending"
        and not str(r.get("refined_at") or "").strip()
    ]
    pending.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    pending = pending[: max(1, int(limit))]
    if not pending:
        return {"ok": True, "status": "skipped", "reason": "no_refinable_candidates", "refined": 0}

    index = _read_index(root)
    beads = (index.get("beads") or {}) if isinstance(index, dict) else {}
    snapshots = [_candidate_snapshot(r, beads) for r in pending]
    payload = {
        "contract": CANDIDATE_REFINEMENT_CONTRACT,
        "run_id": str(run_id or ""),
        "source": str(source or ""),
        "candidate_count": len(snapshots),
        "candidates": snapshots,
    }

    runtime = get_semantic_task_runtime()
    result = runtime.run(
        SemanticTaskRequest(
            root=str(root),
            task_type=TASK_DREAMER_RESEARCH,
            prompt=_prompt() + "\n\nContext JSON:\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True),
            payload=payload,
            idempotency_key=f"candidate_refinement:{run_id}",
            prompt_version=CANDIDATE_REFINEMENT_PROMPT_VERSION,
            rubric_version=CANDIDATE_REFINEMENT_RUBRIC_VERSION,
            output_schema=CANDIDATE_REFINEMENT_CONTRACT,
            model_tier=MODEL_TIER_STANDARD,
            max_tokens=1800,
            temperature=0.2,
            json_mode=True,
            fallback_mode="templated_statement",
            authority_boundary="candidate_only",
            evidence_refs=[
                {"type": "dreamer_candidate", "candidate_id": str(s.get("candidate_id") or "")}
                for s in snapshots
            ],
            metadata={
                "run_id": str(run_id or ""),
                "source": str(source or ""),
                "role": "candidate_refinement",
                "candidate_ids": [str(s.get("candidate_id") or "") for s in snapshots],
            },
        )
    )

    out: dict[str, Any] = {
        "ok": bool(result.ok),
        "status": str(result.status or ""),
        "task_id": str(result.task_id or ""),
        "receipt_id": str(result.receipt_id or ""),
        "eligible": len(pending),
        "refined": 0,
    }
    if result.error:
        out["error"] = result.error
    if not result.ok:
        return out

    payload_out = result.output_json if isinstance(result.output_json, dict) else None
    raw_refinements = (payload_out or {}).get("refinements")
    if not isinstance(raw_refinements, list):
        out["status"] = "succeeded_unparsed"
        return out

    known_ids = {str(r.get("id") or "") for r in pending}
    accepted = [ref for ref in (_valid_refinement(r, known_ids) for r in raw_refinements) if ref]
    if not accepted:
        out["status"] = "succeeded_no_valid_refinements"
        return out

    by_id = {str(r.get("id") or ""): r for r in rows if isinstance(r, dict)}
    recorded_at = _now()
    applied = 0
    for ref in accepted:
        row = by_id.get(ref["candidate_id"])
        if row is None or str(row.get("status") or "").strip().lower() != "pending":
            continue
        if not str(row.get("statement_template") or "").strip():
            row["statement_template"] = str(row.get("statement") or row.get("rationale") or "")
        row["title"] = ref["title"]
        row["statement"] = ref["statement"]
        row["refined_at"] = recorded_at
        row["refinement"] = {
            "source": "semantic_task_runtime",
            "task_type": TASK_DREAMER_RESEARCH,
            "role": "candidate_refinement",
            "task_id": str(result.task_id or ""),
            "receipt_id": str(result.receipt_id or ""),
            "prompt_version": CANDIDATE_REFINEMENT_PROMPT_VERSION,
            "rubric_version": CANDIDATE_REFINEMENT_RUBRIC_VERSION,
        }
        applied += 1

    if applied:
        _write_candidates(root, rows)
    out["refined"] = applied
    return out


__all__ = [
    "CANDIDATE_REFINEMENT_CONTRACT",
    "CANDIDATE_REFINEMENT_PROMPT_VERSION",
    "CANDIDATE_REFINEMENT_RUBRIC_VERSION",
    "refine_pending_candidates",
]
