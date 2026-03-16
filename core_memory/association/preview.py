from __future__ import annotations

"""Deterministic bounded association preview helper.

Used for non-authoritative preview scoring on per-add store writes.
Canonical durable association authorship remains crawler-reviewed.
"""


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in (text or "").replace("_", " ").replace("-", " ").split() if len(t) >= 3}


def _causal_hint_score(text: str) -> int:
    t = (text or "").lower()
    cues = [
        "because",
        "caused",
        "led to",
        "blocked",
        "unblocked",
        "therefore",
        "so that",
        "due to",
    ]
    return sum(1 for c in cues if c in t)


def run_association_pass(index: dict, bead: dict, *, max_lookback: int = 40, top_k: int = 3) -> list[dict]:
    """Compute deterministic association preview candidates.

    Non-destructive: computes derived candidates only.
    Session-relative reasoning is weighted higher than cross-session fallback.
    """
    candidates = []
    new_tags = set((bead.get("tags") or []))
    new_text = str(bead.get("title", "")) + " " + " ".join(bead.get("summary", []) or [])
    new_tokens = _tokenize(new_text)
    new_causal = _causal_hint_score(new_text)

    prior = [b for b in (index.get("beads", {}) or {}).values() if b.get("id") != bead.get("id")]
    prior = sorted(prior, key=lambda b: b.get("created_at", ""), reverse=True)[: max(1, int(max_lookback))]

    for other in prior:
        score = 0
        other_text = str(other.get("title", "")) + " " + " ".join(other.get("summary", []) or [])
        other_tokens = _tokenize(other_text)
        other_causal = _causal_hint_score(other_text)

        shared_tags = sorted(list(new_tags.intersection(set(other.get("tags") or []))))
        if shared_tags:
            score += 3 + min(2, len(shared_tags))

        overlap = len(new_tokens.intersection(other_tokens))
        if overlap:
            score += min(4, overlap)

        same_session = bool(bead.get("session_id") and bead.get("session_id") == other.get("session_id"))
        if same_session:
            score += 2

        if new_causal and other_causal:
            score += 1

        if score <= 0:
            continue

        relationship = "related"
        if new_causal and other_causal and same_session:
            relationship = "supports"
        elif shared_tags:
            relationship = "shared_tag"
        elif same_session:
            relationship = "follows"

        candidates.append(
            {
                "other_id": other.get("id"),
                "relationship": relationship,
                "score": score,
                "shared_tags": shared_tags,
                "same_session": same_session,
                "causal_overlap": bool(new_causal and other_causal),
            }
        )

    candidates = sorted(candidates, key=lambda c: (-int(c.get("score") or 0), str(c.get("other_id") or "")))
    return candidates[: max(0, int(top_k))]
