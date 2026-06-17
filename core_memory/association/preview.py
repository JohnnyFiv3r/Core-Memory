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
        "caused by",
        "led to",
        "blocked",
        "unblocked",
        "therefore",
        "so that",
        "due to",
        "resulted in",
        "as a result",
    ]
    return sum(1 for c in cues if c in t)


def _created_order(left: dict, right: dict) -> int:
    """Return -1 when left appears older than right, 1 when newer, else 0.

    Stored timestamps are ISO-8601 strings in normal operation; lexical ordering is
    deterministic for the canonical format and gracefully degrades to unknown for
    missing/equal values.
    """
    lval = str(left.get("created_at") or "")
    rval = str(right.get("created_at") or "")
    if not lval or not rval or lval == rval:
        return 0
    return -1 if lval < rval else 1


def _relationship_for_candidate(
    *,
    bead: dict,
    other: dict,
    shared_tags: list[str],
    token_overlap: int,
    same_session: bool,
    new_causal: int,
    other_causal: int,
) -> tuple[str, str, str]:
    """Assign a canonical relationship plus auditable heuristic metadata."""
    order = _created_order(bead, other)
    causal_overlap = bool(new_causal and other_causal)

    if causal_overlap and same_session:
        return (
            "supports",
            "causal_overlap_same_session",
            "Both same-session beads contain causal language and overlapping context.",
        )

    if (new_causal or other_causal) and not same_session and (token_overlap or shared_tags):
        if order < 0:
            return (
                "leads_to",
                "causal_cross_session_source_precedes_target",
                "Cross-session causal language with source bead preceding the target bead.",
            )
        return (
            "caused_by",
            "causal_cross_session_source_follows_target",
            "Cross-session causal language with target bead preceding and causing the source bead.",
        )

    if shared_tags:
        return (
            "associated_with",
            "shared_tag_overlap",
            "Beads share one or more tags; no stronger canonical relationship was inferred.",
        )

    if same_session:
        if order < 0:
            return ("precedes", "temporal_precedes", "Source bead appears earlier in the same session.")
        return (
            "associated_with",
            "temporal_follows_requires_endpoint_swap",
            "Source bead appears later in the same session; preview cannot swap endpoints.",
        )

    return (
        "associated_with",
        "lexical_overlap",
        "Beads share lexical context; no stronger canonical relationship was inferred.",
    )


def infer_relationship(bead_a: dict, bead_b: dict) -> tuple[str, str]:
    """Return (relationship, reason_code) for two beads using the preview classifier.

    Intended as a fallback when an agent-authored association omits the relationship field.
    Produces a canonical relationship or a legacy inverse alias that association
    write boundaries can endpoint-normalize.
    """
    new_text = str(bead_a.get("title", "")) + " " + " ".join(bead_a.get("summary") or [])
    other_text = str(bead_b.get("title", "")) + " " + " ".join(bead_b.get("summary") or [])
    new_tokens = _tokenize(new_text)
    other_tokens = _tokenize(other_text)
    new_tags = set(bead_a.get("tags") or [])
    other_tags = set(bead_b.get("tags") or [])
    shared_tags = sorted(new_tags.intersection(other_tags))
    overlap = len(new_tokens.intersection(other_tokens))
    same_session = bool(
        bead_a.get("session_id") and bead_a.get("session_id") == bead_b.get("session_id")
    )
    new_causal = _causal_hint_score(new_text)
    other_causal = _causal_hint_score(other_text)

    rel, reason_code, _ = _relationship_for_candidate(
        bead=bead_a,
        other=bead_b,
        shared_tags=shared_tags,
        token_overlap=overlap,
        same_session=same_session,
        new_causal=new_causal,
        other_causal=other_causal,
    )
    return rel, reason_code


def compute_preview_association_candidates(index: dict, bead: dict, *, max_lookback: int = 40, top_k: int = 3) -> list[dict]:
    """Compute deterministic association preview candidates.

    Non-destructive: computes derived candidates only.
    Session-relative reasoning is weighted higher than cross-session fallback.

    Note: previously exported as ``run_association_pass``, which collided with
    the unrelated crawler-update applier
    ``runtime.passes.association_pass.run_association_pass``. The old name
    remains as a compatibility alias.
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

        relationship, reason_code, reason_text = _relationship_for_candidate(
            bead=bead,
            other=other,
            shared_tags=shared_tags,
            token_overlap=overlap,
            same_session=same_session,
            new_causal=new_causal,
            other_causal=other_causal,
        )

        candidates.append(
            {
                "other_id": other.get("id"),
                "relationship": relationship,
                "score": score,
                "shared_tags": shared_tags,
                "same_session": same_session,
                "causal_overlap": bool(new_causal and other_causal),
                "reason_code": reason_code,
                "reason_text": reason_text,
            }
        )

    candidates = sorted(candidates, key=lambda c: (-int(c.get("score") or 0), str(c.get("other_id") or "")))
    return candidates[: max(0, int(top_k))]


# Compatibility alias — see compute_preview_association_candidates docstring.
run_association_pass = compute_preview_association_candidates
