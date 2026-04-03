from __future__ import annotations

import re

_STOP = {
    "the", "and", "for", "with", "that", "this", "what", "when", "why", "did", "was", "are", "about", "into", "from", "where"
}


def stem_lite(tok: str) -> str:
    t = str(tok or "")
    if t.endswith("ing") and len(t) > 5:
        t = t[:-3]
    elif t.endswith("ed") and len(t) > 4:
        t = t[:-2]
    elif t.endswith("s") and len(t) > 4:
        t = t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    raw = re.sub(r"[^\w\s\"']+", " ", str(text or "").lower())
    raw = re.sub(r"\s+", " ", raw).strip()
    out: list[str] = []
    for t in raw.replace('"', ' ').split():
        if t in _STOP or len(t) < 3:
            continue
        out.append(stem_lite(t))
    return out


def normalize_query(query: str) -> dict:
    raw = re.sub(r"[^\w\s\"']+", " ", str(query or "").lower())
    raw = re.sub(r"\s+", " ", raw).strip()
    phrases = re.findall(r'"([^"]+)"', raw)
    if not phrases:
        toks = [t for t in raw.split() if t]
        phrases = [" ".join(toks[i : i + 2]) for i in range(max(0, len(toks) - 1))]
    return {
        "raw_normalized": raw,
        "tokens": tokenize(raw),
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

