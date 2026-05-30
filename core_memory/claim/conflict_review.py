"""Render-agnostic conflict review prompts (#14 UX layer).

Core never renders a UI and never parses the user's reply. It hands the agent a
*render-agnostic* payload: the two contested values, when each was recorded, a
natural-language question the agent can speak in its own words, and a small set
of resolution choices with stable ids. The agent surfaces it conversationally,
interprets the user's free-text answer, and calls back with the chosen
``resolution``. No harness needs a button/card UI.

Resolution semantics map to canonical claim-update decisions:

  prefer_a      → supersede the B claim with the A claim   (A becomes current)
  prefer_b      → supersede the A claim with the B claim   (B becomes current)
  retract_both  → retract both claims                      (nothing current)
  defer         → no write; leave the conflict unresolved

The chosen resolution is written through ``process_turn_finalized`` →
``emit_claim_updates`` like any other agent-judged claim update.
"""
from __future__ import annotations

import uuid
from typing import Any

RESOLUTION_PREFER_A = "prefer_a"
RESOLUTION_PREFER_B = "prefer_b"
RESOLUTION_RETRACT_BOTH = "retract_both"
RESOLUTION_DEFER = "defer"
RESOLUTION_BOTH_VALID = "both_valid"

RESOLUTION_CHOICES = {
    RESOLUTION_PREFER_A,
    RESOLUTION_PREFER_B,
    RESOLUTION_RETRACT_BOTH,
    RESOLUTION_DEFER,
    RESOLUTION_BOTH_VALID,
}


def _value(claim: dict[str, Any]) -> str:
    raw = (claim or {}).get("value")
    if raw is None:
        return "(no value recorded)"
    return str(raw)


def _recorded_at(claim: dict[str, Any]) -> str:
    return str((claim or {}).get("created_at") or (claim or {}).get("observed_at") or "").strip()


def _age_days(conflict_since: str) -> int | None:
    from core_memory.temporal import normalize_as_of
    from datetime import datetime, timezone

    if not conflict_since:
        return None
    dt = normalize_as_of(conflict_since)
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, int((now - dt).total_seconds() // 86400))


def _date_clause(recorded_at: str) -> str:
    return f" (recorded {recorded_at})" if recorded_at else ""


def build_conflict_review(
    *,
    subject: str,
    slot: str,
    claim_a: dict[str, Any],
    claim_b: dict[str, Any],
    epistemic_conflict_score: float,
    conflict_since: str = "",
    candidate_id: str = "",
) -> dict[str, Any]:
    """Build a render-agnostic review payload for a single (subject, slot) conflict.

    The agent reads ``question`` and ``agent_instructions``, surfaces them in its
    own words, then maps the user's free-text reply to one ``resolutions[].choice``.
    """
    value_a = _value(claim_a)
    value_b = _value(claim_b)
    date_a = _recorded_at(claim_a)
    date_b = _recorded_at(claim_b)
    age = _age_days(conflict_since)

    age_clause = ""
    if age is not None and age > 0:
        age_clause = f"They've disagreed for {age} day{'s' if age != 1 else ''} with no resolution. "

    question = (
        f"I have two conflicting values on record for {subject}'s {slot}: "
        f"\"{value_a}\"{_date_clause(date_a)} and \"{value_b}\"{_date_clause(date_b)}. "
        f"{age_clause}Which one is current — or should I drop both?"
    )

    resolutions = [
        {
            "choice": RESOLUTION_PREFER_A,
            "value": value_a,
            "claim_id": str((claim_a or {}).get("id") or ""),
            "effect": f"Make \"{value_a}\" the current value and supersede \"{value_b}\".",
        },
        {
            "choice": RESOLUTION_PREFER_B,
            "value": value_b,
            "claim_id": str((claim_b or {}).get("id") or ""),
            "effect": f"Make \"{value_b}\" the current value and supersede \"{value_a}\".",
        },
        {
            "choice": RESOLUTION_RETRACT_BOTH,
            "value": None,
            "claim_id": "",
            "effect": "Drop both values; record nothing as current for this slot.",
        },
        {
            "choice": RESOLUTION_DEFER,
            "value": None,
            "claim_id": "",
            "effect": "Leave it unresolved for now; don't change anything.",
        },
        {
            "choice": RESOLUTION_BOTH_VALID,
            "value": None,
            "claim_id": "",
            "effect": (
                f"Both \"{value_a}\" and \"{value_b}\" are true — but in different contexts. "
                "Requires two non-empty scope labels: one for each value. "
                "Offer 'default / everywhere else' as an explicit option for whichever scope is broader."
            ),
        },
    ]

    agent_instructions = (
        "Surface this contradiction to the user conversationally, in your own words — "
        "present both values and ask which is current. Do NOT pick a side yourself. "
        "Read the user's free-text reply and map it to exactly one resolution choice id "
        "from `resolutions`. If they clearly mean one value, use prefer_a/prefer_b; if "
        "they say both are wrong, retract_both; if they're unsure or say not now, defer. "
        "If they say both are true but in different contexts, use both_valid — but you MUST "
        "then ask for a scope label for each side before calling apply_reviewed_proposal: "
        "'When is \"" + value_a + "\" true?' and 'When is \"" + value_b + "\" true?' "
        "If they name only one scope, ask once where the other still holds; "
        "offer 'default / everywhere else' as an explicit option for the broader case. "
        "Do NOT call apply_reviewed_proposal for both_valid until you have both scope labels. "
        "Then call apply_reviewed_proposal with candidate_id, decision=\"accept\", "
        "resolution=\"both_valid\", context_a=<scope for value_a>, context_b=<scope for value_b>. "
        "For any other resolution, call apply_reviewed_proposal with this candidate_id, "
        "decision=\"accept\", and resolution=<the chosen choice id>."
    )

    return {
        "kind": "contradiction_review",
        "candidate_id": str(candidate_id or ""),
        "subject": subject,
        "slot": slot,
        "epistemic_conflict_score": round(float(epistemic_conflict_score), 6),
        "conflict_since": conflict_since,
        "age_days": age,
        "question": question,
        "resolutions": resolutions,
        "agent_instructions": agent_instructions,
    }


def resolution_to_claim_updates(
    *,
    resolution: str,
    subject: str,
    slot: str,
    claim_a_id: str,
    claim_b_id: str,
    trigger_bead_id: str,
    reason: str = "",
) -> list[dict[str, Any]]:
    """Translate a chosen resolution into canonical claim-update rows.

    Returns rows suitable for ``crawler_updates.claim_updates`` consumed by
    ``emit_claim_updates``. ``defer`` returns an empty list (no write).
    """
    r = str(resolution or "").strip().lower()
    reason_text = str(reason or "").strip() or "user-resolved contradiction via conflict review"
    a = str(claim_a_id or "").strip()
    b = str(claim_b_id or "").strip()

    def _supersede(target: str, replacement: str) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "decision": "supersede",
            "target_claim_id": target,
            "replacement_claim_id": replacement,
            "subject": subject,
            "slot": slot,
            "reason_text": reason_text,
            "trigger_bead_id": trigger_bead_id,
            "provenance": "conflict_review_resolution",
        }

    def _retract(target: str) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "decision": "retract",
            "target_claim_id": target,
            "replacement_claim_id": None,
            "subject": subject,
            "slot": slot,
            "reason_text": reason_text,
            "trigger_bead_id": trigger_bead_id,
            "provenance": "conflict_review_resolution",
        }

    if r == RESOLUTION_PREFER_A:
        if not a or not b:
            return []
        return [_supersede(target=b, replacement=a)]
    if r == RESOLUTION_PREFER_B:
        if not a or not b:
            return []
        return [_supersede(target=a, replacement=b)]
    if r == RESOLUTION_RETRACT_BOTH:
        rows = []
        if a:
            rows.append(_retract(a))
        if b:
            rows.append(_retract(b))
        return rows
    # both_valid and defer reach here. both_valid is handled upstream in candidates.py
    # (the existing candidate is marked pending for re-review). defer means no write now.
    return []


__all__ = [
    "RESOLUTION_PREFER_A",
    "RESOLUTION_PREFER_B",
    "RESOLUTION_RETRACT_BOTH",
    "RESOLUTION_DEFER",
    "RESOLUTION_BOTH_VALID",
    "RESOLUTION_CHOICES",
    "build_conflict_review",
    "resolution_to_claim_updates",
]
