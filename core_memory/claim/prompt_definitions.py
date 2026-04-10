"""Plain-language definitions for claim kinds, update decisions, and slot semantics."""

CLAIM_KIND_DEFINITIONS = {
    "preference": "A stated like, dislike, want, or prioritization by the subject.",
    "identity": "A fact about who the subject is — name, role, location, relationship status.",
    "policy": "A rule, constraint, or standing instruction that governs behavior.",
    "commitment": "A stated intention or promise about future action.",
    "condition": "A contingent statement: if/when/unless some condition holds.",
    "relationship": "A connection between the subject and another entity.",
    "location": "A place the subject is associated with.",
    "custom": "A claim that doesn't fit the above categories.",
}

CLAIM_UPDATE_DECISION_DEFINITIONS = {
    "reaffirm": "The claim remains accurate; no change needed.",
    "supersede": "The old claim is replaced by a newer, more accurate value.",
    "retract": "The claim is withdrawn and should no longer be used.",
    "conflict": "Two claims disagree and resolution requires disambiguation.",
}

SLOT_SEMANTICS = {
    "preference": "Slot names should describe the domain of preference (e.g., 'food', 'music', 'communication_style').",
    "identity": "Slot names should describe the identity facet (e.g., 'name', 'occupation', 'city').",
    "policy": "Slot names should describe the policy domain (e.g., 'response_format', 'language').",
    "commitment": "Slot names should describe the commitment type (e.g., 'deadline', 'next_action').",
    "condition": "Slot names should describe the condition trigger (e.g., 'context', 'when').",
}

GROUNDING_EXPECTATIONS = """
Claims must be grounded in observable turn content.
- Do not infer claims from single-word responses.
- Require at least one sentence with a subject and predicate.
- reason_text must quote or paraphrase the source evidence.
- confidence < 0.5 claims should be omitted unless no better signal exists.
"""
