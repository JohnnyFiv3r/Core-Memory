from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.schema.normalization import normalize_relation_type


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def events_dir(root: str | Path) -> Path:
    p = Path(root) / ".beads" / "events"
    p.mkdir(parents=True, exist_ok=True)
    return p


def candidates_path(root: str | Path) -> Path:
    return events_dir(root) / "dreamer-candidates.json"


def read_candidates(root: str | Path) -> list[dict[str, Any]]:
    p = candidates_path(root)
    if not p.exists():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
    except Exception:
        pass
    return []


def write_candidates(root: str | Path, rows: list[dict[str, Any]]) -> None:
    p = candidates_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def relationship_signal(row_or_relationship: Any, fallback: str = "") -> str:
    if isinstance(row_or_relationship, dict):
        for key in ("relationship_signal", "relationship_raw", "relationship"):
            value = str(row_or_relationship.get(key) or "").strip().lower()
            if value:
                return value
        return str(fallback or "").strip().lower()
    return str(row_or_relationship or fallback or "").strip().lower()


def proposal_family(hypothesis_type: str) -> str:
    ht = str(hypothesis_type or "").strip().lower()
    if ht == "contradiction_candidate":
        return "contradiction"
    if ht == "entity_merge_candidate":
        return "entity_identity"
    if ht == "retrieval_value_candidate":
        return "retrieval_value"
    if ht == "proposed_theme_candidate":
        return "theme"
    return "association"


def benchmark_tags_for_hypothesis(hypothesis_type: str) -> list[str]:
    ht = str(hypothesis_type or "").strip().lower()
    if ht == "contradiction_candidate":
        return ["contradiction_update", "current_state_factual"]
    if ht == "entity_merge_candidate":
        return ["entity_coreference"]
    if ht == "retrieval_value_candidate":
        return ["causal_mechanism", "current_state_factual", "entity_coreference"]
    if ht in {"transferable_lesson_candidate", "abstraction_candidate", "precedent_candidate"}:
        return ["causal_mechanism"]
    if ht == "proposed_theme_candidate":
        return ["causal_mechanism"]
    return ["causal_mechanism", "current_state_factual"]


def candidate_key(row: dict[str, Any]) -> str:
    ht = str(row.get("hypothesis_type") or "")
    if ht == "proposed_theme_candidate":
        related = sorted(str(b) for b in (row.get("related_bead_ids") or []) if str(b))
        return "|".join(["proposed_theme_candidate", relationship_signal(row), *related])
    return "|".join(
        [
            ht,
            str(row.get("source_bead_id") or ""),
            str(row.get("target_bead_id") or ""),
            str(row.get("relationship") or ""),
            str(row.get("relationship_signal") or ""),
            str(row.get("source_entity_id") or ""),
            str(row.get("target_entity_id") or ""),
        ]
    )


def make_candidate_row(
    *,
    now: str,
    run_meta: dict[str, Any],
    association: dict[str, Any],
    hypothesis_type: str,
    rationale: str,
    expected_decision_impact: str,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    src = str(association.get("source") or association.get("source_bead_id") or "").strip()
    tgt = str(association.get("target") or association.get("target_bead_id") or "").strip()
    rel_raw = str(association.get("relationship") or "similar_pattern").strip() or "similar_pattern"
    rel = normalize_relation_type(rel_raw)
    signal = relationship_signal(association, rel_raw)

    row = {
        "id": f"dc-{uuid.uuid4().hex[:12]}",
        "created_at": now,
        "status": "pending",
        "hypothesis_type": hypothesis_type,
        "proposal_family": proposal_family(hypothesis_type),
        "benchmark_tags": benchmark_tags_for_hypothesis(hypothesis_type),
        "source_bead_id": src,
        "target_bead_id": tgt,
        "relationship": rel,
        "relationship_signal": signal,
        "relationship_raw": rel_raw if rel_raw.lower() != rel else str(association.get("relationship_raw") or ""),
        "novelty": float(association.get("novelty") or 0.0),
        "grounding": float(association.get("grounding") or 0.0),
        "confidence": float(association.get("confidence") or 0.0),
        "rationale": str(rationale),
        "expected_decision_impact": str(expected_decision_impact),
        "run_metadata": {
            "run_id": str(run_meta.get("run_id") or ""),
            "mode": str(run_meta.get("mode") or "suggest"),
            "source": str(run_meta.get("source") or "side_effect_queue"),
            "session_id": str(run_meta.get("session_id") or ""),
            "flush_tx_id": str(run_meta.get("flush_tx_id") or ""),
            "novel_only": bool(run_meta.get("novel_only", True)),
            "seen_window_runs": int(run_meta.get("seen_window_runs") or 0),
            "max_exposure": int(run_meta.get("max_exposure") or -1),
        },
        "raw": dict(association),
    }
    if extras:
        row.update(dict(extras))
    return row


def enqueue_contradiction_pressure_candidates(
    *,
    root: str | Path,
    conflicts: list[Any],
    threshold: float | None = None,
    run_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit contradiction_pressure_candidate rows for conflicts above threshold."""
    env_threshold = float(os.environ.get("CORE_MEMORY_CONFLICT_REVIEW_THRESHOLD") or "0.7")
    effective_threshold = float(threshold) if threshold is not None else env_threshold

    rows = read_candidates(root)
    now = now_iso()
    run_meta = dict(run_metadata or {})
    existing_by_key: dict[str, dict[str, Any]] = {}
    for r in rows:
        if isinstance(r, dict):
            existing_by_key.setdefault(candidate_key(r), r)

    added = 0
    candidate_ids: dict[str, str] = {}
    deferred_keys: list[str] = []

    for conflict in list(conflicts or []):
        if hasattr(conflict, "to_dict"):
            c = conflict.to_dict()
        elif hasattr(conflict, "__dict__"):
            c = dict(conflict.__dict__)
        elif isinstance(conflict, dict):
            c = conflict
        else:
            continue

        score = float(c.get("epistemic_conflict_score") or 0.0)
        if score <= effective_threshold:
            continue

        subject = str(c.get("subject") or "").strip()
        slot = str(c.get("slot") or "").strip()
        claim_a_id = str(c.get("claim_a_id") or "").strip()
        claim_b_id = str(c.get("claim_b_id") or "").strip()
        if not subject or not slot:
            continue

        slot_key = f"{subject}:{slot}"
        association: dict[str, Any] = {
            "source": claim_a_id,
            "target": claim_b_id,
            "relationship": "contradicts",
            "novelty": 0.0,
            "grounding": score,
            "confidence": score,
        }
        row = make_candidate_row(
            now=now,
            run_meta=run_meta,
            association=association,
            hypothesis_type="contradiction_pressure_candidate",
            rationale=(
                f"Claim conflict on {subject}:{slot} has epistemic pressure score {score:.3f} "
                f"(chain_seq_gap={c.get('chain_seq_gap', 0)}, conflict_since={c.get('conflict_since', '')}). "
                "Human review recommended."
            ),
            expected_decision_impact=(
                f"Resolve conflicting claims on '{subject}' / '{slot}' to improve recall accuracy."
            ),
            extras={
                "subject": subject,
                "slot": slot,
                "claim_a_id": claim_a_id,
                "claim_b_id": claim_b_id,
                "epistemic_conflict_score": score,
                "conflict_since": str(c.get("conflict_since") or ""),
                "chain_seq_gap": int(c.get("chain_seq_gap") or 0),
                "conflict_threshold": effective_threshold,
            },
        )
        row["hypothesis_type"] = "contradiction_pressure_candidate"
        row["proposal_family"] = "contradiction"
        row["benchmark_tags"] = ["contradiction_update", "current_state_factual"]

        k = candidate_key(row)
        existing = existing_by_key.get(k)
        if existing is not None:
            state = str(existing.get("review_state") or existing.get("status") or "").strip().lower()
            if state in {"deferred", "rejected", "accepted"}:
                deferred_keys.append(slot_key)
            else:
                candidate_ids[slot_key] = str(existing.get("id") or "")
            continue

        rows.append(row)
        existing_by_key[k] = row
        candidate_ids[slot_key] = str(row.get("id") or "")
        added += 1

    if added:
        write_candidates(root, rows)

    return {
        "ok": True,
        "added": added,
        "queue_depth": len(rows),
        "threshold": effective_threshold,
        "candidate_ids": candidate_ids,
        "deferred_keys": deferred_keys,
        "path": str(candidates_path(root)),
    }


__all__ = [
    "benchmark_tags_for_hypothesis",
    "candidate_key",
    "candidates_path",
    "enqueue_contradiction_pressure_candidates",
    "events_dir",
    "make_candidate_row",
    "now_iso",
    "proposal_family",
    "read_candidates",
    "relationship_signal",
    "write_candidates",
]
