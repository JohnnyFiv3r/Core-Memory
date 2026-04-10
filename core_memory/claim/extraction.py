"""Heuristic claim extraction (no LLM calls)."""

from __future__ import annotations

import re
import uuid

from core_memory.schema.models import Claim

# Keyword patterns for inferring claim kinds
PREFERENCE_KEYWORDS = ["prefer", "like", "love", "enjoy", "hate", "dislike", "want", "favorite"]
IDENTITY_KEYWORDS = ["am", "i'm", "i am", "my name", "i work", "occupation", "role"]
POLICY_KEYWORDS = ["always", "never", "must", "should", "require", "policy"]
COMMITMENT_KEYWORDS = ["will", "promise", "commit", "plan to", "going to"]
CONDITION_KEYWORDS = ["if", "when", "unless", "until", "given", "timezone"]
LOCATION_KEYWORDS = ["live in", "based in", "from", "located in", "currently in"]


_CLAUSE_SPLIT_RE = re.compile(r"[.!?;]+|\s+and\s+(?=(?:i\b|i'm\b|i\sam\b|my\b))", re.IGNORECASE)


def _sanitize_slot(text: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", str(text or "").strip().lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "general"


def infer_claim_kind(text: str) -> str:
    """Infer claim kind from text keywords."""
    lower = str(text or "").lower()

    if any(k in lower for k in LOCATION_KEYWORDS):
        return "location"
    if "timezone" in lower:
        return "condition"
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


def _extract_preference(clause: str) -> dict | None:
    lower = clause.lower()
    if not any(k in lower for k in PREFERENCE_KEYWORDS):
        return None

    m = re.search(r"\b(?:i\s+)?(?:prefer|like|love|enjoy|hate|dislike|want)\s+(.+)$", clause, re.IGNORECASE)
    if not m:
        return None

    value = m.group(1).strip(" ,")
    slot = "preference"
    m_for = re.search(r"\bfor\s+([a-zA-Z0-9_\-\s]{2,40})$", value, re.IGNORECASE)
    if m_for:
        topic = _sanitize_slot(m_for.group(1))
        slot = f"preference_{topic}"

    return {
        "claim_kind": "preference",
        "subject": "user",
        "slot": slot,
        "value": value[:200],
        "confidence": 0.78,
    }


def _extract_timezone(clause: str) -> dict | None:
    if "timezone" not in clause.lower():
        return None
    m = re.search(r"\b(?:my\s+)?timezone\s*(?:is|=|:)\s*([^,.;!?]+)", clause, re.IGNORECASE)
    if not m:
        return None
    value = m.group(1).strip()
    # Defensive trim for assistant-preface bleed (e.g. "... America/Chicago Noted")
    value = re.sub(r"\s+(?:noted|okay|ok|thanks|thank\s+you).*$", "", value, flags=re.IGNORECASE).strip()
    if not value:
        return None
    return {
        "claim_kind": "condition",
        "subject": "user",
        "slot": "timezone",
        "value": value[:120],
        "confidence": 0.86,
    }


def _extract_location(clause: str) -> dict | None:
    m = re.search(
        r"\b(?:i\s+live\s+in|i\s*(?:am|'m)\s+in|based\s+in|located\s+in|currently\s+in)\s+([^,.;!?]+)",
        clause,
        re.IGNORECASE,
    )
    if not m:
        return None
    value = m.group(1).strip()
    if not value:
        return None
    return {
        "claim_kind": "location",
        "subject": "user",
        "slot": "location",
        "value": value[:120],
        "confidence": 0.82,
    }


def _extract_identity(clause: str) -> dict | None:
    m_name = re.search(r"\bmy\s+name\s+is\s+([^,.;!?]+)", clause, re.IGNORECASE)
    if m_name:
        return {
            "claim_kind": "identity",
            "subject": "user",
            "slot": "name",
            "value": m_name.group(1).strip()[:120],
            "confidence": 0.9,
        }

    m_role = re.search(r"\bi\s+(?:am|'m)\s+(?:a\s+|an\s+)?([^,.;!?]{2,80})", clause, re.IGNORECASE)
    if m_role:
        role = m_role.group(1).strip()
        low = role.lower()
        if low and low not in {"in", "going", "will", "glad", "happy"}:
            return {
                "claim_kind": "identity",
                "subject": "user",
                "slot": "role",
                "value": role[:120],
                "confidence": 0.68,
            }
    return None


def _extract_policy(clause: str) -> dict | None:
    lower = clause.lower()
    if not any(k in lower for k in POLICY_KEYWORDS):
        return None
    return {
        "claim_kind": "policy",
        "subject": "user",
        "slot": "policy",
        "value": clause.strip()[:200],
        "confidence": 0.74,
    }


def _extract_commitment(clause: str) -> dict | None:
    lower = clause.lower()
    if not any(k in lower for k in COMMITMENT_KEYWORDS):
        return None
    return {
        "claim_kind": "commitment",
        "subject": "user",
        "slot": "commitment",
        "value": clause.strip()[:200],
        "confidence": 0.72,
    }


def _extract_condition(clause: str) -> dict | None:
    lower = clause.lower()
    if not any(k in lower for k in CONDITION_KEYWORDS):
        return None
    return {
        "claim_kind": "condition",
        "subject": "user",
        "slot": "condition",
        "value": clause.strip()[:200],
        "confidence": 0.64,
    }


def _extract_claim_from_clause(clause: str) -> dict | None:
    for extractor in (
        _extract_timezone,
        _extract_location,
        _extract_preference,
        _extract_identity,
        _extract_policy,
        _extract_commitment,
        _extract_condition,
    ):
        out = extractor(clause)
        if out:
            return out
    return None


def _to_claim_row(clause: str, parsed: dict) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "claim_kind": str(parsed.get("claim_kind") or infer_claim_kind(clause)),
        "subject": str(parsed.get("subject") or "user"),
        "slot": str(parsed.get("slot") or "custom"),
        "value": parsed.get("value") if parsed.get("value") is not None else clause[:200],
        "reason_text": f"Extracted from turn clause: {clause[:120]}",
        "confidence": float(parsed.get("confidence") or 0.6),
    }
    # keep within schema via dataclass conversion
    return Claim.from_dict(row).to_dict()


def extract_claims(user_query: str, assistant_final: str, context_beads: list[dict]) -> list[dict]:
    """
    Heuristic claim extraction from conversation turn.
    Returns list of claim dicts matching Claim.from_dict() contract.
    No LLM calls - rule/keyword based only.
    """
    _ = context_beads  # reserved for future signal blending
    claims: list[dict] = []
    seen_value: set[tuple[str, str, str]] = set()
    seen_slot: set[tuple[str, str]] = set()

    # Process sources separately to avoid cross-source clause pollution.
    # Priority: user_query first, assistant_final second.
    sources = [
        ("user_query", str(user_query or "")),
        ("assistant_final", str(assistant_final or "")),
    ]

    for source_name, source_text in sources:
        text = source_text.strip()
        if not text:
            continue
        clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(text) if c and c.strip()]

        for clause in clauses:
            if len(clause) < 6:
                continue
            parsed = _extract_claim_from_clause(clause)
            if not parsed:
                # fallback only when signal exists
                kind = infer_claim_kind(clause)
                if kind == "custom":
                    continue
                parsed = {
                    "claim_kind": kind,
                    "subject": "user",
                    "slot": kind,
                    "value": clause[:200],
                    "confidence": 0.58,
                }

            row = _to_claim_row(clause, parsed)
            slot_key = (
                str(row.get("subject") or "").strip().lower(),
                str(row.get("slot") or "").strip().lower(),
            )
            value_key = (
                slot_key[0],
                slot_key[1],
                str(row.get("value") or "").strip().lower(),
            )

            if value_key in seen_value:
                continue

            # Keep the first claim per subject+slot by source priority (user before assistant).
            if slot_key in seen_slot:
                continue

            seen_slot.add(slot_key)
            seen_value.add(value_key)
            row["reason_text"] = f"Extracted from {source_name}: {clause[:120]}"
            claims.append(row)

    return claims
