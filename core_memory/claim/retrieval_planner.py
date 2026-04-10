"""
Claim-aware retrieval mode planner.
Decides which retrieval mode to use based on query signals and current claim state.
"""
from __future__ import annotations

RETRIEVAL_MODES = {
    "fact_first": "Prioritize exact fact/claim matches for direct questions.",
    "causal_first": "Prioritize causal/reasoning beads for why/how questions.",
    "temporal_first": "Prioritize recent beads for time-sensitive queries.",
    "mixed": "Balanced approach for general queries.",
}

# Signal words for each mode
FACT_SIGNALS = ["what is", "what's", "who is", "where is", "when is", "how old", "what are", "tell me about"]
CAUSAL_SIGNALS = ["why", "how", "reason", "because", "explain", "caused", "result"]
TEMPORAL_SIGNALS = ["recently", "latest", "last", "current", "now", "today", "this week"]


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
    if not query:
        return "mixed"

    lower = query.lower()

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

    # Get IDs of beads that have active claims
    active_claim_bead_ids = set()
    for slot_data in current_state["slots"].values():
        current = slot_data.get("current_claim")
        if current and slot_data.get("status") == "active":
            # The bead ID isn't stored directly on claim in v1 — skip boost for now
            pass

    # For now, return as-is (boost logic requires bead_id on claims, reserved for v2)
    return results
