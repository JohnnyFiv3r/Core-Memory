from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core_memory.entity.retrieval import bead_entity_match_score
from core_memory.temporal import parse_timestamp


HISTORICAL_HINTS = {"previous", "previously", "history", "historical", "before", "earlier", "used", "last", "ago"}
CURRENT_HINTS = {"current", "now", "today", "latest", "stance", "active", "present"}


def _terms(text: str) -> set[str]:
    return {
        t.strip(" ?!.,:;()[]{}\"'`").lower()
        for t in str(text or "").split()
        if len(t.strip()) >= 2
    }


def _query_historical(query: str, as_of: str | None = None) -> bool:
    if str(as_of or "").strip():
        return True
    q = _terms(query)
    return bool(q.intersection(HISTORICAL_HINTS))


def _query_current(query: str, as_of: str | None = None) -> bool:
    if str(as_of or "").strip():
        return False
    q = _terms(query)
    return bool(q.intersection(CURRENT_HINTS))


def _bead_text(bead: dict[str, Any], row: dict[str, Any]) -> str:
    return " ".join(
        [
            str(bead.get("title") or ""),
            " ".join(str(x) for x in (bead.get("summary") or [])),
            str(bead.get("detail") or ""),
            str(row.get("semantic_text") or ""),
            str(row.get("lexical_text") or ""),
        ]
    ).lower()


def _claim_hint_terms(claim_state: dict[str, Any] | None) -> set[str]:
    out: set[str] = set()
    if not isinstance(claim_state, dict):
        return out
    slots = claim_state.get("slots") or {}
    if not isinstance(slots, dict):
        return out
    for key, slot_data in slots.items():
        if not isinstance(slot_data, dict):
            continue
        if str(slot_data.get("status") or "") != "active":
            continue
        out.update(_terms(str(key or "")))
        cur = slot_data.get("current_claim") or {}
        if isinstance(cur, dict):
            out.update(_terms(str(cur.get("claim_kind") or "")))
            out.update(_terms(str(cur.get("value") or "")))
    return out


def _temporal_fit(bead: dict[str, Any], *, as_of: str | None, query_is_historical: bool) -> float:
    now = datetime.now(timezone.utc)
    as_of_dt = parse_timestamp(as_of) if as_of else None

    start = parse_timestamp(bead.get("effective_from") or bead.get("observed_at") or bead.get("recorded_at") or bead.get("created_at"))
    end = parse_timestamp(bead.get("effective_to"))

    if as_of_dt is not None:
        if start is not None and as_of_dt < start:
            return 0.1
        if end is not None and as_of_dt >= end:  # exclusive
            return 0.05
        return 1.0

    if query_is_historical:
        if end is not None:
            return 0.85
        if start is not None and (now - start).days > 30:
            return 0.75
        return 0.55

    # current queries prefer still-effective entries
    if end is not None and end <= now:
        return 0.2
    return 0.8


def _recency_score(bead: dict[str, Any], *, historical: bool) -> float:
    ts = parse_timestamp(bead.get("created_at") or bead.get("recorded_at") or bead.get("observed_at"))
    if ts is None:
        return 0.5
    age_days = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0)
    if historical:
        return min(1.0, age_days / 365.0)
    return max(0.0, 1.0 - min(age_days / 365.0, 1.0))


def rerank_semantic_rows(
    *,
    rows: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    query: str,
    retrieval_mode: str,
    claim_state: dict[str, Any] | None,
    as_of: str | None,
    entity_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    q_terms = _terms(query)
    claim_terms = _claim_hint_terms(claim_state)
    historical = _query_historical(query, as_of)
    current_query = _query_current(query, as_of)

    # Soft mode hints (not hard walls)
    weights = {
        "semantic": 0.34,
        "lexical": 0.16,
        "claim": 0.18,
        "entity": 0.10,
        "temporal": 0.14,
        "structural": 0.08,
        "recency": 0.00,
    }
    if retrieval_mode == "fact_first":
        weights.update({"claim": 0.26, "entity": 0.18, "semantic": 0.26, "lexical": 0.16, "temporal": 0.10, "structural": 0.04, "recency": 0.00})
    elif retrieval_mode == "causal_first":
        weights.update({"semantic": 0.30, "lexical": 0.12, "claim": 0.10, "entity": 0.06, "temporal": 0.10, "structural": 0.24, "recency": 0.08})
    elif retrieval_mode == "temporal_first":
        weights.update({"semantic": 0.24, "lexical": 0.12, "claim": 0.10, "entity": 0.06, "temporal": 0.34, "structural": 0.08, "recency": 0.06})

    rescored: list[dict[str, Any]] = []
    for row in rows:
        r = dict(row or {})
        bid = str(r.get("bead_id") or "")
        corpus_row = dict((by_id.get(bid) or {}))
        bead = dict(corpus_row.get("bead") or {})

        semantic = float(r.get("semantic_score") if r.get("semantic_score") is not None else r.get("score") or 0.0)
        blob = _bead_text(bead, corpus_row)
        lexical = min(1.0, len(q_terms.intersection(_terms(blob))) / max(1.0, len(q_terms) or 1.0)) if q_terms else 0.0

        claim = 1.0 if str(r.get("anchor_reason") or "") == "claim_current_state" else 0.0
        if claim < 1.0 and claim_terms:
            claim = min(1.0, len(claim_terms.intersection(_terms(blob))) / max(1.0, len(claim_terms)))

        entity, entity_hits = bead_entity_match_score(bead, entity_context)

        temporal = _temporal_fit(bead, as_of=as_of, query_is_historical=historical)
        structural = min(1.0, float(r.get("context_bias_score") or 0.0))
        recency = _recency_score(bead, historical=historical)
        retrieval_value_bonus = float(bead.get("retrieval_value_bonus") or 0.0)
        myelination_bonus = float(bead.get("myelination_bonus") or 0.0)

        supersedes_count = int(bead.get("supersedes_count") or 0)
        superseded_by_count = int(bead.get("superseded_by_count") or 0)
        contradicts_count = int(bead.get("contradicts_count") or 0)

        supersession_penalty = 0.0
        if bead.get("superseded_by"):
            supersession_penalty += 0.28
        if superseded_by_count > 0:
            supersession_penalty += min(0.25, 0.12 * superseded_by_count)
        # F-S1: check status instead of validity (validity collapsed into status)
        status_val = str(bead.get("status") or "").lower()
        if status_val in {"superseded", "archived"}:
            supersession_penalty += 0.18

        if historical:
            supersession_penalty *= 0.35

        conflict_penalty = 0.0
        if bead.get("decision_conflict_with"):
            conflict_penalty += 0.2
        if bead.get("contradicts"):
            conflict_penalty += 0.15
        if contradicts_count > 0:
            conflict_penalty += min(0.22, 0.10 * contradicts_count)

        current_truth_bonus = 0.0
        if current_query and supersedes_count > 0 and superseded_by_count == 0:
            current_truth_bonus += min(0.18, 0.08 + 0.04 * supersedes_count)
        if historical and superseded_by_count > 0:
            current_truth_bonus += min(0.12, 0.05 + 0.03 * superseded_by_count)

        rank_score = (
            weights["semantic"] * semantic
            + weights["lexical"] * lexical
            + weights["claim"] * claim
            + weights["entity"] * entity
            + weights["temporal"] * temporal
            + weights["structural"] * structural
            + weights["recency"] * recency
            + current_truth_bonus
            + retrieval_value_bonus
            + myelination_bonus
            - supersession_penalty
            - conflict_penalty
        )

        r.setdefault("semantic_score", semantic)
        r["rank_score"] = max(0.0, float(rank_score))
        r["feature_scores"] = {
            "semantic": round(semantic, 4),
            "lexical": round(lexical, 4),
            "claim_match": round(claim, 4),
            "entity_match": round(entity, 4),
            "entity_hits": entity_hits,
            "temporal_fit": round(temporal, 4),
            "structural": round(structural, 4),
            "recency": round(recency, 4),
            "retrieval_value_bonus": round(retrieval_value_bonus, 4),
            "myelination_bonus": round(myelination_bonus, 4),
            "supersession_penalty": round(supersession_penalty, 4),
            "conflict_penalty": round(conflict_penalty, 4),
            "current_truth_bonus": round(current_truth_bonus, 4),
            "supersedes_count": int(supersedes_count),
            "superseded_by_count": int(superseded_by_count),
            "contradicts_count": int(contradicts_count),
        }
        # Keep score aligned with rank for downstream sorting while preserving semantic_score separately.
        r["score"] = r["rank_score"]
        rescored.append(r)

    rescored.sort(key=lambda x: float(x.get("rank_score") or 0.0), reverse=True)
    return rescored
