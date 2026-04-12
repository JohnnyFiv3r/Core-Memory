"""
Claim-aware retrieval mode planner.
Decides which retrieval mode to use based on query signals and current claim state.
"""
from __future__ import annotations

from typing import Any

RETRIEVAL_MODES = {
    "fact_first": "Prioritize exact fact/claim matches for direct questions.",
    "causal_first": "Prioritize causal/reasoning beads for why/how questions.",
    "temporal_first": "Prioritize recent beads for time-sensitive queries.",
    "mixed": "Balanced approach for general queries.",
}

# Signal words for each mode
FACT_SIGNALS = [
    "what is",
    "what's",
    "who is",
    "where is",
    "when is",
    "how old",
    "what are",
    "tell me about",
    "prefer",
    "preference",
    "policy",
    "timezone",
]
CAUSAL_SIGNALS = ["why", "how", "reason", "because", "explain", "caused", "result"]
TEMPORAL_SIGNALS = ["recently", "latest", "last", "current", "now", "today", "this week"]
HISTORICAL_SIGNALS = ["used to", "previously", "before", "earlier", "history", "historical", "as of"]


def _terms(text: str) -> set[str]:
    import re

    s = str(text or "").lower().replace("_", " ").replace("-", " ")
    return {tok for tok in re.findall(r"[a-z0-9]+", s) if len(tok) >= 3}


def _active_slot_hints(current_state: dict | None) -> set[str]:
    hints: set[str] = set()
    if not isinstance(current_state, dict):
        return hints
    slots = current_state.get("slots") or {}
    if not isinstance(slots, dict):
        return hints
    for key, slot_data in slots.items():
        if not isinstance(slot_data, dict):
            continue
        if str(slot_data.get("status") or "") != "active":
            continue
        key_s = str(key or "")
        subject, _, slot = key_s.partition(":")
        if subject:
            hints.add(subject.lower())
        if slot:
            hints.add(slot.lower())
        current = slot_data.get("current_claim") or {}
        if isinstance(current, dict):
            for field in ("claim_kind", "subject", "slot"):
                v = str(current.get(field) or "").strip().lower()
                if v:
                    hints.add(v)
            value = current.get("value")
            if isinstance(value, str):
                hints.update(_terms(value))
    return hints


def plan_retrieval_mode(query: str, catalog: dict | None, current_state: dict | None) -> str:
    """
    Plan retrieval mode based on query signals and available claim state.

    Args:
        query: User query string
        catalog: Optional catalog dict (beads, relations, etc.)
        current_state: Optional current claim state from resolve_all_current_state()

    Returns:
        One of: fact_first, causal_first, temporal_first, mixed
    """
    q = str(query or "")
    if not q.strip():
        return "mixed"

    lower = q.lower()
    q_terms = _terms(lower)

    active_hints = _active_slot_hints(current_state)
    if active_hints and q_terms.intersection(active_hints):
        return "fact_first"

    if any(signal in lower for signal in HISTORICAL_SIGNALS):
        return "temporal_first"

    catalog_claim_hints: set[str] = set()
    if isinstance(catalog, dict):
        for k in (catalog.get("claim_kinds") or []):
            ks = str(k or "").strip().lower()
            if ks:
                catalog_claim_hints.add(ks)
        for k in (catalog.get("topic_keys") or []):
            ks = str(k or "").strip().lower()
            if ks:
                catalog_claim_hints.add(ks)

    if catalog_claim_hints and q_terms.intersection(catalog_claim_hints):
        return "fact_first"

    # Check if query matches a known subject+slot in current state
    if current_state and current_state.get("slots"):
        for slot_key in current_state["slots"]:
            subject, _, slot = slot_key.partition(":")
            if subject.lower() in lower or slot.lower() in lower:
                return "fact_first"

    # Check causal signals
    if any(signal in lower for signal in CAUSAL_SIGNALS):
        return "causal_first"

    # Check temporal signals
    if any(signal in lower for signal in TEMPORAL_SIGNALS):
        return "temporal_first"

    # Check fact signals
    if any(signal in lower for signal in FACT_SIGNALS):
        return "fact_first"

    return "mixed"


def boost_claim_results(results: list[dict], current_state: dict | None) -> list[dict]:
    """
    Re-rank results by claim relevance.
    Beads with active claims for the queried subject+slot are boosted.

    Args:
        results: List of retrieval result dicts (each should have a 'score' key)
        current_state: Current claim state from resolve_all_current_state()

    Returns:
        Re-ranked list
    """
    if not current_state or not current_state.get("slots"):
        return results

    hints = _active_slot_hints(current_state)
    if not hints:
        return results

    boosted: list[dict] = []
    for row in list(results or []):
        if not isinstance(row, dict):
            continue
        r = dict(row)
        base = float(r.get("score") or 0.0)
        blob = " ".join(
            [
                str(r.get("title") or ""),
                " ".join(str(x) for x in (r.get("summary") or [])),
                str(r.get("detail") or ""),
                str(r.get("semantic_text") or ""),
                str(r.get("lexical_text") or ""),
            ]
        ).lower()
        overlap = len(_terms(blob).intersection(hints))
        if overlap > 0:
            r["score"] = min(1.0, base + min(0.12, 0.03 * overlap))
            r["claim_boost"] = True
        else:
            r["claim_boost"] = False
        boosted.append(r)

    boosted.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return boosted
