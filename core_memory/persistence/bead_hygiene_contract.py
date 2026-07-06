from __future__ import annotations

import re
from typing import Optional


def clean_title(title: str) -> str:
    t = (title or "").strip()
    t = re.sub(r"^\s*\[\[\s*reply_to_current\s*\]\]\s*", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:120]


GENERIC_TITLE_PATTERNS = [
    r"^turn memory$",
    r"^reply$",
    r"^update$",
    r"^current turn$",
    r"^bridge[_\s-]*ok$",
    r"^acknowledged$",
    r"^done$",
    r"^ok$",
]


RUNTIME_META_PATTERNS = [
    r"openclaw runtime context",
    r"inter-session message",
    r"subagent_announce",
    r"reply_skip",
    r"\bbridge[_\s-]*ok\b",
]


def is_generic_title(title: str) -> bool:
    t = (title or "").strip().lower()
    if not t:
        return True
    return any(re.search(p, t, re.IGNORECASE) for p in GENERIC_TITLE_PATTERNS)


def rewrite_generic_title(title: str) -> str:
    t = clean_title(title)
    if not is_generic_title(t):
        return t
    if re.search(r"bridge[_\s-]*ok", t or "", re.IGNORECASE):
        return "bridge acknowledgement"
    return "assistant turn"


def is_runtime_meta_chatter(user_query: str = "", assistant_final: str = "") -> bool:
    txt = f"{user_query} {assistant_final}".lower()
    return any(re.search(p, txt, re.IGNORECASE) for p in RUNTIME_META_PATTERNS)


def extract_entities(text: str) -> list[str]:
    txt = text or ""
    cands = set(re.findall(r"\b[A-Za-z][A-Za-z0-9_.:/-]{2,}\b", txt))
    stop = {
        "the", "and", "with", "from", "this", "that", "these", "those", "there", "here",
        "what", "when", "where", "which", "who", "why", "how",
    }
    out = []
    for c in sorted(cands):
        if c.lower() in stop:
            continue
        if c.startswith("bead-"):
            continue
        out.append(c)
    return out[:20]


def extract_state_change(text: str) -> Optional[dict]:
    t = text or ""
    m = re.search(r"(?:changed|switched|migrated|replaced)\s+from\s+(.+?)\s+to\s+(.+?)(?:[\.;\n]|$)", t, re.IGNORECASE)
    if not m:
        return None
    return {"from": m.group(1).strip(), "to": m.group(2).strip()}


def extract_validity(text: str) -> Optional[str]:
    """Extract validity from text. DEPRECATED (F-S1): use status field instead."""
    t = (text or "").lower()
    if any(k in t for k in ["superseded", "replaced by", "deprecated"]):
        return "superseded"
    if any(k in t for k in ["closed", "resolved"]):
        return "closed"
    if any(k in t for k in ["transient", "temporary"]):
        return "transient"
    if t.strip():
        return "open"
    return None


def _information_signals(bead: dict) -> dict:
    title = str(bead.get("title") or "")
    summary = " ".join(bead.get("summary") or [])
    detail = str(bead.get("detail") or "")
    because = bead.get("because") or []
    supporting = bead.get("supporting_facts") or []
    evidence_refs = bead.get("evidence_refs") or []
    state_change = bead.get("state_change") or extract_state_change(f"{title} {summary} {detail}")
    entities = bead.get("entities") or extract_entities(f"{title} {summary} {detail}")
    validity = bead.get("validity") or extract_validity(f"{title} {summary} {detail}")

    return {
        "has_entities": bool(entities),
        "has_state_change": bool(state_change),
        "has_because": bool(because),
        "has_supporting": bool(supporting),
        "has_evidence_refs": bool(evidence_refs),
        "has_validity": bool(validity),
        "is_runtime_meta": is_runtime_meta_chatter(str(bead.get("user_query") or ""), f"{title} {summary} {detail}"),
        "is_generic_title": is_generic_title(title),
    }


def classify_bead_richness(bead: dict) -> str:
    """Classify write richness as LOW or NORMAL."""
    s = _information_signals(bead)
    positives = sum(1 for k in ["has_entities", "has_state_change", "has_because", "has_supporting", "has_evidence_refs", "has_validity"] if s.get(k))
    if s.get("is_runtime_meta"):
        return "LOW"
    if positives >= 2 and not s.get("is_generic_title"):
        return "NORMAL"
    return "LOW"


def can_be_retrieval_eligible(bead: dict) -> bool:
    """Bead is eligible when it has a meaningful title and a recognized canonical type."""
    from core_memory.schema.normalization import CANONICAL_BEAD_TYPES, normalize_bead_type
    title_ok = not is_generic_title(str(bead.get("title") or ""))
    type_ok = normalize_bead_type(str(bead.get("type") or "")) in CANONICAL_BEAD_TYPES
    return title_ok and type_ok


def enforce_bead_hygiene_contract(bead: dict) -> dict:
    """Normalize bead to thin/rich hygiene contract without rejecting thin beads."""
    out = dict(bead or {})
    out["title"] = rewrite_generic_title(str(out.get("title") or ""))

    out.setdefault("summary", [])
    out.setdefault("session_id", out.get("session_id"))
    out.setdefault("source_turn_ids", out.get("source_turn_ids") or [])
    if out.get("prev_bead_id") is None:
        out["prev_bead_id"] = out.get("prev_bead_id")

    richness = classify_bead_richness(out)
    out["bead_richness"] = richness
    out["retrieval_eligible"] = bool(out.get("retrieval_eligible", True))
    return out


__all__ = [
    "GENERIC_TITLE_PATTERNS",
    "RUNTIME_META_PATTERNS",
    "can_be_retrieval_eligible",
    "classify_bead_richness",
    "clean_title",
    "enforce_bead_hygiene_contract",
    "extract_entities",
    "extract_state_change",
    "extract_validity",
    "is_generic_title",
    "is_runtime_meta_chatter",
    "rewrite_generic_title",
]
