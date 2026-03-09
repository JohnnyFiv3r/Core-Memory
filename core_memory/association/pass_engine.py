from __future__ import annotations


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in (text or "").replace("_", " ").replace("-", " ").split() if len(t) >= 3}


def run_association_pass(index: dict, bead: dict, *, max_lookback: int = 40, top_k: int = 3) -> list[dict]:
    """Deterministic association pass scaffold (V2-P4 Step 3).

    Non-destructive: computes derived candidates only.
    """
    candidates = []
    new_tags = set((bead.get("tags") or []))
    new_tokens = _tokenize(str(bead.get("title", "")) + " " + " ".join(bead.get("summary", []) or []))

    prior = [b for b in (index.get("beads", {}) or {}).values() if b.get("id") != bead.get("id")]
    prior = sorted(prior, key=lambda b: b.get("created_at", ""), reverse=True)[: max(1, int(max_lookback))]

    for other in prior:
        score = 0
        shared_tags = sorted(list(new_tags.intersection(set(other.get("tags") or []))))
        if shared_tags:
            score += 3 + min(2, len(shared_tags))

        other_tokens = _tokenize(str(other.get("title", "")) + " " + " ".join(other.get("summary", []) or []))
        overlap = len(new_tokens.intersection(other_tokens))
        if overlap:
            score += min(3, overlap)

        if bead.get("session_id") and bead.get("session_id") == other.get("session_id"):
            score += 1

        if score <= 0:
            continue

        relationship = "related"
        if shared_tags:
            relationship = "shared_tag"
        elif bead.get("session_id") and bead.get("session_id") == other.get("session_id"):
            relationship = "follows"

        candidates.append(
            {
                "other_id": other.get("id"),
                "relationship": relationship,
                "score": score,
                "shared_tags": shared_tags,
            }
        )

    candidates = sorted(candidates, key=lambda c: (-int(c.get("score") or 0), str(c.get("other_id") or "")))
    return candidates[: max(0, int(top_k))]
