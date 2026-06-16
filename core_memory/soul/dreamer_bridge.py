"""Dreamer → SOUL bridge (PRD: docs/PRD/soul-files.md §8.2, §9.1, §13.4).

Dreamer never authors SOUL. It studies evidence and surfaces *findings*; the
bridge turns eligible findings into **proposed** SOUL revisions that wait for
human approval before they fold into the projection (§8.2: "Dreamer findings do
not directly write SOUL — they trigger agent review"; §9.1 approval-required
flow). Every proposal is therefore ``source="dreamer"``,
``epistemic_status="inferred"`` and ``requires_approval=True`` — Dreamer findings
are evidence, not authority (§10).

Eligible candidate types (from the Dreamer candidate queue) and their targets:

- ``tension_candidate``              → ``TENSIONS.md`` (surfaced goal/value tension)
- ``goal_candidate``                 → ``GOALS.md``    (latent goal to consider)
- ``goal_decay_warning``             → ``GOALS.md``    (dormant goal flagged for review)
- ``value_candidate``                → ``IDENTITY.md`` (emergent value from behavior)
- ``identity_divergence_candidate``  → ``IDENTITY.md`` (endorsed-identity drift)

Proposals are idempotent: a finding maps to a stable ``entry_key`` and the bridge
skips any ``(target_file, entry_key)`` that a prior Dreamer-sourced revision
already covers, so repeated runs never churn duplicate proposals.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.runtime.dreamer.candidates import _read_candidates
from core_memory.soul.store import propose_soul_update, soul_history

# Candidate hypothesis_type → (target_file, entry_key prefix). The prefix keeps
# distinct finding families from colliding inside a shared file (goal_candidate
# and goal_decay_warning both land in GOALS.md).
_BRIDGE_MAP: dict[str, tuple[str, str]] = {
    "tension_candidate": ("TENSIONS.md", "tension"),
    "goal_candidate": ("GOALS.md", "goal"),
    "goal_decay_warning": ("GOALS.md", "decay"),
    "value_candidate": ("IDENTITY.md", "value"),
    "identity_divergence_candidate": ("IDENTITY.md", "divergence"),
}

# Candidate field carrying the stable identifier, per hypothesis_type.
_IDENT_FIELD: dict[str, str] = {
    "tension_candidate": "tension_key",
    "goal_candidate": "goal_theme",
    "goal_decay_warning": "goal_bead_id",
    "value_candidate": "value_theme",
    "identity_divergence_candidate": "identity_entry_key",
}


def _finding_entry_key(candidate: dict[str, Any]) -> str | None:
    """Stable, dedupe-safe entry key for an eligible candidate, or None."""
    ht = str(candidate.get("hypothesis_type") or "").strip()
    spec = _BRIDGE_MAP.get(ht)
    if spec is None:
        return None
    _, prefix = spec
    ident = str(candidate.get(_IDENT_FIELD.get(ht, "")) or "").strip()
    if not ident:
        return None
    return f"{prefix}:{ident}"


def _evidence_from(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for bid in candidate.get("supporting_bead_ids") or []:
        b = str(bid or "").strip()
        if b:
            refs.append({"bead_id": b, "relationship": "supports"})
    return refs


def propose_soul_from_dreamer(
    root: str | Path,
    *,
    subject: str = "self",
    limit: int = 200,
) -> dict[str, Any]:
    """Generate proposed SOUL revisions from pending Dreamer findings.

    Reads the Dreamer candidate queue, maps eligible *pending* findings to
    proposed SOUL revisions (always human-approval-gated), and deduplicates
    against prior Dreamer-sourced revisions by ``(target_file, entry_key)``.
    Returns a summary; never raises for an empty queue.
    """
    candidates = _read_candidates(root)

    # Existing coverage by stable key, regardless of source. A prior Dreamer
    # revision (proposed/applied/rejected) means the finding is already in
    # review — don't re-propose. A human/agent revision for the same key means
    # the entry is already owned by an authoritative author; proposing a Dreamer
    # duplicate would, if approved, clobber that endorsed content during folding
    # (last applied upsert per key wins). Either way: leave it alone.
    history = soul_history(root, subject=subject, limit=100000)
    covered: set[tuple[str, str]] = set()
    for rev in history.get("revisions") or []:
        covered.add((str(rev.get("target_file") or ""), str(rev.get("entry_key") or "")))

    proposed = 0
    skipped = 0
    revision_ids: list[str] = []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        if str(cand.get("status") or "").strip().lower() != "pending":
            continue
        ht = str(cand.get("hypothesis_type") or "").strip()
        spec = _BRIDGE_MAP.get(ht)
        if spec is None:
            continue
        target_file, _ = spec
        entry_key = _finding_entry_key(cand)
        if not entry_key:
            continue
        if (target_file, entry_key) in covered:
            skipped += 1
            continue

        content = str(cand.get("statement") or cand.get("rationale") or "").strip()
        if not content:
            skipped += 1
            continue

        out = propose_soul_update(
            root,
            target_file=target_file,
            entry_key=entry_key,
            content=content,
            op="upsert",
            subject=subject,
            source="dreamer",
            epistemic_status="inferred",
            reason=f"Dreamer finding ({ht}) surfaced for self-model review.",
            evidence=_evidence_from(cand),
            requires_approval=True,
        )
        if out.get("ok"):
            proposed += 1
            covered.add((target_file, entry_key))
            rid = str(out.get("revision_id") or "")
            if rid:
                revision_ids.append(rid)
        else:
            skipped += 1

        if proposed >= max(1, int(limit)):
            break

    return {
        "ok": True,
        "subject": subject,
        "proposed": proposed,
        "skipped": skipped,
        "revision_ids": revision_ids,
    }


__all__ = ["propose_soul_from_dreamer"]
