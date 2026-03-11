"""
Failure pattern detection for repeated hypothesis failure tracking.

Moved from store.py per Codex Phase 2 refactor.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional


def compute_failure_signature(plan: str) -> str:
    """Compute a stable failure signature hash from a plan string."""
    norm = re.sub(r"\s+", " ", (plan or "").strip().lower())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def find_failure_signature_matches(
    index: dict,
    plan: str,
    limit: int = 5,
    context_tags: Optional[list[str]] = None
) -> list[dict]:
    """Find prior FAILED_HYPOTHESIS beads matching the normalized failure signature.

    Warn-only preflight retrieval for Phase 2. Returns deterministic newest-first matches.
    """
    sig = compute_failure_signature(plan)
    beads = index.get("beads", {})
    matches = []
    req_tags = set([str(t).strip() for t in (context_tags or []) if str(t).strip()])

    for bead in beads.values():
        if bead.get("type") != "failed_hypothesis":
            continue
        if bead.get("failure_signature") != sig:
            continue

        bead_tags = set([str(t).strip() for t in (bead.get("tags") or []) if str(t).strip()])
        tag_overlap = len(req_tags.intersection(bead_tags)) if req_tags else 0
        matches.append(
            {
                "bead_id": bead.get("id"),
                "title": bead.get("title"),
                "failure_signature": sig,
                "created_at": bead.get("created_at"),
                "summary": (bead.get("summary") or [])[:2],
                "tag_overlap": tag_overlap,
            }
        )

    matches = sorted(matches, key=lambda m: (-(m.get("tag_overlap") or 0), m.get("created_at") or ""), reverse=False)
    matches = list(reversed(matches))  # newest first within overlap bucket
    return matches[: max(0, int(limit))]


def preflight_failure_check(
    index: dict,
    plan: str,
    limit: int = 5,
    context_tags: Optional[list[str]] = None
) -> dict:
    """Warn-only preflight check for repeated failure patterns.

    No hard blocking here. Caller can escalate policy later.
    """
    sig = compute_failure_signature(plan)
    matches = find_failure_signature_matches(index, plan, limit=limit, context_tags=context_tags)
    return {
        "ok": True,
        "mode": "warn_only",
        "failure_signature": sig,
        "match_count": len(matches),
        "matches": matches,
        "recommendation": "warn" if matches else "proceed",
    }
