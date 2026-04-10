"""Heuristic claim extraction (no LLM calls)."""

from typing import Optional
import re
import uuid
from core_memory.schema.models import Claim
from core_memory.schema.normalization import CANONICAL_CLAIM_KINDS, normalize_claim_kind

# Keyword patterns for inferring claim kinds
PREFERENCE_KEYWORDS = ["prefer", "like", "love", "enjoy", "hate", "dislike", "want", "favorite"]
IDENTITY_KEYWORDS = ["am", "i'm", "i am", "my name", "i work", "i live", "i have"]
POLICY_KEYWORDS = ["always", "never", "must", "should", "require", "policy"]
COMMITMENT_KEYWORDS = ["will", "promise", "commit", "plan to", "going to"]
CONDITION_KEYWORDS = ["if", "when", "unless", "until", "given"]


def infer_claim_kind(text: str) -> str:
    """Infer claim kind from text keywords."""
    lower = text.lower()
    if any(k in lower for k in PREFERENCE_KEYWORDS):
        return "preference"
    if any(k in lower for k in IDENTITY_KEYWORDS):
        return "identity"
    if any(k in lower for k in POLICY_KEYWORDS):
        return "policy"
    if any(k in lower for k in COMMITMENT_KEYWORDS):
        return "commitment"
    if any(k in lower for k in CONDITION_KEYWORDS):
        return "condition"
    return "custom"


def extract_claims(user_query: str, assistant_final: str, context_beads: list[dict]) -> list[dict]:
    """
    Heuristic claim extraction from conversation turn.
    Returns list of claim dicts matching Claim.from_dict() contract.
    No LLM calls - keyword-based only.
    """
    claims = []

    combined = f"{user_query} {assistant_final}".strip()
    if not combined:
        return claims

    # Simple sentence extraction - split on periods, question marks, etc.
    sentences = re.split(r'[.!?]+', combined)

    seen = set()  # dedup by (subject, slot)

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 10:
            continue

        kind = infer_claim_kind(sentence)
        if kind == "custom":
            continue  # only extract when we have signal

        # Heuristic subject/slot extraction
        words = sentence.lower().split()
        subject = "user"  # default subject
        slot = kind  # default slot is the kind itself
        value = sentence[:200]  # value is the sentence text truncated

        dedup_key = (subject, slot, value[:50])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        claim = {
            "id": str(uuid.uuid4()),
            "claim_kind": kind,
            "subject": subject,
            "slot": slot,
            "value": value,
            "reason_text": f"Extracted from turn: {sentence[:100]}",
            "confidence": 0.6,
        }
        claims.append(claim)

    return claims
