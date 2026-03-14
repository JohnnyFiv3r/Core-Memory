from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from core_memory.policy.incidents import load_incidents, incident_match_strength

_STOP = {"the", "and", "for", "with", "that", "this", "what", "when", "why", "did", "was", "are", "about", "into", "from", "where"}


def _tokenize(text: str) -> set[str]:
    """Tokenize text into searchable terms."""
    return {t.lower() for t in (text or "").replace("_", " ").replace("-", " ").split() if len(t) >= 3}


def _is_memory_intent(text: str) -> bool:
    """Detect if query indicates memory recall intent."""
    q = (text or "").lower()
    cues = [
        "remember",
        "what did we decide",
        "earlier",
        "last time",
        "previous",
        "why did we",
        "recall",
        "history",
        "find memory",
    ]
    return any(c in q for c in cues)


def _expand_query_tokens(text: str, base_tokens: set[str], max_extra: int = 24) -> set[str]:
    """Bounded synonym/entity expansion for better deterministic recall hits."""
    q = (text or "").lower()
    expanded = set(base_tokens)

    phrase_map = {
        "openclaw only": {"single", "orchestrator", "openclaw", "migration", "adapter"},
        "single orchestrator": {"openclaw", "migration", "multi", "orchestrator"},
        "multi orchestrator": {"adapter", "pydanticai", "springai", "emit_turn_finalized", "integration", "port"},
        "multiple orchestrator": {"adapter", "pydanticai", "springai", "emit_turn_finalized", "integration", "port"},
        "core adapters": {"adapter", "integration", "emit_turn_finalized", "pydanticai", "springai"},
        "switch": {"migration", "transition"},
        "migrate": {"migration", "transition"},
        "transition": {"migration", "switch"},
    }

    token_map = {
        "openclaw": {"orchestrator", "adapter"},
        "pydanticai": {"adapter", "integration"},
        "springai": {"adapter", "integration"},
        "emit_turn_finalized": {"integration", "port", "adapter"},
        "orchestrator": {"framework", "adapter"},
        "adapter": {"integration", "orchestrator"},
        "migration": {"transition", "switch"},
    }

    extras: list[str] = []
    for phrase, words in phrase_map.items():
        if phrase in q:
            extras.extend(list(words))

    for t in list(base_tokens):
        for w in token_map.get(t, set()):
            extras.append(w)

    for w in extras:
        if len(expanded) >= len(base_tokens) + max(0, int(max_extra)):
            break
        expanded.add(w)

    return expanded


def _stem_lite(tok: str) -> str:
    t = tok
    if t.endswith("ing") and len(t) > 5:
        t = t[:-3]
    elif t.endswith("ed") and len(t) > 4:
        t = t[:-2]
    elif t.endswith("s") and len(t) > 4:
        t = t[:-1]
    return t


def normalize_query(query: str) -> dict:
    raw = (query or "").strip().lower()
    raw = re.sub(r"[^\w\s\"']+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()

    phrases = re.findall(r'"([^"]+)"', raw)
    if not phrases:
        toks = [t for t in raw.split() if t]
        phrases = [" ".join(toks[i:i+2]) for i in range(len(toks)-1)]

    tokens = []
    for t in raw.replace('"', ' ').split():
        if t in _STOP or len(t) < 3:
            continue
        tokens.append(_stem_lite(t))

    return {
        "raw_normalized": raw,
        "tokens": tokens,
        "phrases": [p.strip() for p in phrases if p.strip()],
    }


def classify_intent(query: str) -> dict:
    qn = normalize_query(query)
    q = qn["raw_normalized"]

    if any(x in q for x in ["why", "because", "rationale", "what happened", "caused"]):
        intent_class = "causal"
    elif any(x in q for x in ["what changed", "changed", "updated", "replaced", "supersed"]):
        intent_class = "what_changed"
    elif any(x in q for x in ["when", "date", "time", "timeline"]):
        intent_class = "when"
    else:
        intent_class = "remember"

    return {
        "intent_class": intent_class,
        "causal_intent": intent_class == "causal",
        "query_type_bucket": intent_class,
        "normalized": qn,
    }


def _load_topic_aliases(root: Path) -> list[dict]:
    p = Path(__file__).parents[1] / "data" / "topic_aliases.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def resolve_query_anchors(query: str, root: Path) -> dict:
    qn = normalize_query(query)
    qraw = qn["raw_normalized"]
    qtokens = set(qraw.split())

    matched_incidents = []
    expansions = []
    for row in load_incidents(root):
        iid = str(row.get("incident_id") or "")
        st = float(incident_match_strength(query, iid, root))
        if st <= 0:
            continue
        matched_incidents.append({"incident_id": iid, "strength": st})
        expansions.extend(iid.replace("_", " ").split())

    matched_topics = []
    for row in _load_topic_aliases(root):
        tk = str(row.get("topic_key") or "")
        aliases = [str(a or "").lower() for a in (row.get("aliases") or [])]
        strength = 0.0
        for a in aliases:
            an = " ".join(a.replace("_", " ").replace("-", " ").split())
            if an and an in qraw:
                strength = max(strength, 1.0)
            else:
                ats = set(an.split())
                if ats and qtokens.intersection(ats):
                    strength = max(strength, 0.5)
        if strength > 0:
            matched_topics.append({"topic_key": tk, "strength": strength})
            expansions.extend(tk.replace("_", " ").split())

    expanded_query = " ".join([qraw] + expansions).strip()
    expanded_query = re.sub(r"\s+", " ", expanded_query)

    return {
        "normalized": qn,
        "matched_incidents": sorted(matched_incidents, key=lambda x: (-x["strength"], x["incident_id"])),
        "matched_topics": sorted(matched_topics, key=lambda x: (-x["strength"], x["topic_key"])),
        "expanded_query": expanded_query,
    }
