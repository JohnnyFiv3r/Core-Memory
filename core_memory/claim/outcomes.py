"""
Memory interaction outcome classification.
Describes how memory steered a turn — separate from claims.
"""

INTERACTION_ROLES = {
    "memory_resolution": "Memory directly answered a question or resolved ambiguity.",
    "memory_correction": "Memory corrected or contradicted an assumption in the turn.",
    "memory_reflection": "Memory context was surfaced to inform but not directly answer.",
}

def classify_memory_outcome(turn_context: dict) -> dict | None:
    """
    Classify how memory was used in a turn.

    Args:
        turn_context: dict with optional keys:
            - retrieved_beads: list of beads retrieved for this turn
            - query: the user query string
            - used_memory: bool indicating if memory was materially used
            - correction_triggered: bool if memory corrected something
            - reflection_triggered: bool if memory was surfaced as context

    Returns:
        dict with {interaction_role, memory_outcome} or None if memory wasn't used.
    """
    if not turn_context:
        return None

    used_memory = turn_context.get("used_memory", False)
    retrieved_beads = turn_context.get("retrieved_beads", [])

    # If no memory was retrieved or used, return None
    if not used_memory and not retrieved_beads:
        return None

    correction_triggered = turn_context.get("correction_triggered", False)
    reflection_triggered = turn_context.get("reflection_triggered", False)

    if correction_triggered:
        role = "memory_correction"
        outcome = {
            "role": role,
            "description": INTERACTION_ROLES[role],
            "bead_count": len(retrieved_beads),
        }
    elif reflection_triggered:
        role = "memory_reflection"
        outcome = {
            "role": role,
            "description": INTERACTION_ROLES[role],
            "bead_count": len(retrieved_beads),
        }
    elif retrieved_beads or used_memory:
        role = "memory_resolution"
        outcome = {
            "role": role,
            "description": INTERACTION_ROLES[role],
            "bead_count": len(retrieved_beads),
        }
    else:
        return None

    return {
        "interaction_role": role,
        "memory_outcome": outcome,
    }
