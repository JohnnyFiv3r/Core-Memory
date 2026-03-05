from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path


def _tokenize(text: str) -> list[str]:
    return [t for t in (text or "").lower().replace("_", " ").replace("-", " ").split() if len(t) >= 3]


def _field_tokens(bead: dict) -> dict[str, list[str]]:
    return {
        "type": _tokenize(str(bead.get("type") or "")),
        "title": _tokenize(str(bead.get("title") or "")),
        "summary": _tokenize(" ".join(bead.get("summary") or [])),
        "tags": _tokenize(" ".join(bead.get("tags") or [])),
        "incident": _tokenize(str(bead.get("incident_id") or "")),
    }


def lexical_lookup(root: Path, query: str, k: int = 8) -> dict:
    index_file = root / ".beads" / "index.json"
    if not index_file.exists():
        return {"ok": False, "error": "index_missing"}
    idx = json.loads(index_file.read_text(encoding="utf-8"))
    beads = list((idx.get("beads") or {}).values())
    q_tokens = _tokenize(query)
    if not q_tokens:
        return {"ok": True, "backend": "lexical-field-tfidf", "query": query, "results": []}

    field_weights = {
        "title": 2.6,
        "tags": 3.0,
        "incident": 3.0,
        "summary": 1.2,
        "type": 1.0,
    }

    docs = []
    df = Counter()
    for b in beads:
        f = _field_tokens(b)
        merged = []
        for fk, toks in f.items():
            w = int(round(field_weights.get(fk, 1.0) * 10))
            merged.extend(toks * max(1, w))
        docs.append((str(b.get("id") or ""), str(b.get("type") or ""), str(b.get("status") or ""), merged))
        for t in set(merged):
            df[t] += 1

    N = max(1, len(docs))
    scored = []
    for bead_id, typ, status, toks in docs:
        if not bead_id:
            continue
        tf = Counter(toks)
        score = 0.0
        for qt in q_tokens:
            if tf.get(qt, 0) <= 0:
                continue
            idf = math.log((1 + N) / (1 + df.get(qt, 0))) + 1.0
            score += (1.0 + math.log(tf[qt])) * idf
        if score > 0:
            scored.append({"bead_id": bead_id, "score": float(score), "type": typ, "status": status})

    scored = sorted(scored, key=lambda x: (x.get("score", 0.0), x.get("bead_id", "")), reverse=True)
    return {"ok": True, "backend": "lexical-field-tfidf", "query": query, "results": scored[: max(1, int(k))]}
