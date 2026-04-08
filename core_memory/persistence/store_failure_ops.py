from __future__ import annotations

from typing import Any, Optional

from core_memory.retrieval.failure_patterns import (
    compute_failure_signature,
    find_failure_signature_matches,
    preflight_failure_check,
)


def compute_failure_signature_for_store(store: Any, plan: str) -> str:
    return compute_failure_signature(plan)


def find_failure_signature_matches_for_store(
    store: Any,
    *,
    plan: str = "",
    limit: int = 5,
    context_tags: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
) -> list[dict]:
    """Compatibility wrapper for failure-signature matching.

    Legacy callers may pass `tags=[...]` only; map that to a deterministic
    plan string and/or context_tags for ranking.
    """
    index = store._read_json(store.beads_dir / "index.json")

    tags_n = [str(t).strip().lower() for t in (tags or []) if str(t).strip()]
    plan_n = str(plan or "").strip()

    # Legacy ranking behavior: when only tags are provided, rank failed_hypothesis
    # by tag overlap first, then recency.
    if not plan_n and tags_n:
        req = set(tags_n)
        rows = []
        for b in (index.get("beads") or {}).values():
            if str(b.get("type") or "").strip().lower() != "failed_hypothesis":
                continue
            bt = set(str(t).strip().lower() for t in (b.get("tags") or []) if str(t).strip())
            ov = len(req.intersection(bt))
            if ov <= 0:
                continue
            row = dict(b)
            row["tag_overlap"] = ov
            rows.append(row)
        rows.sort(key=lambda r: (int(r.get("tag_overlap") or 0), str(r.get("created_at") or "")), reverse=True)
        return rows[: max(1, int(limit))]

    if not plan_n and tags_n:
        plan_n = " ".join(tags_n)
    ctx_n = context_tags if context_tags is not None else (tags_n or None)

    return find_failure_signature_matches(index, plan_n, limit=limit, context_tags=ctx_n)


def preflight_failure_check_for_store(
    store: Any,
    *,
    plan: str,
    limit: int = 5,
    context_tags: Optional[list[str]] = None,
) -> dict:
    index = store._read_json(store.beads_dir / "index.json")
    return preflight_failure_check(index, plan, limit=limit, context_tags=context_tags)


__all__ = [
    "compute_failure_signature_for_store",
    "find_failure_signature_matches_for_store",
    "preflight_failure_check_for_store",
]
