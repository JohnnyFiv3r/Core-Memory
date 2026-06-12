"""Worldline convergence detection — the storyline overlay candidate source.

Narratives are earned, not ambient: the dreamer proposes an interpretation
only when the continuity structure itself crosses a threshold — multiple
worldlines repeatedly intersecting in the same beads (kind diversity counts
for more than same-kind pileup). Below threshold, no candidate, ever. The
explicit decide flow then accepts or rejects; only accepted candidates
materialise as storyline overlays.

Deterministic: same store ⇒ same candidates. Statements are templated from
structure — refinement into richer prose is a later, clearly-marked LLM step;
the detector itself never calls a model.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.graph.worldlines import derive_worldlines


def _min_worldlines() -> int:
    try:
        return max(2, int(os.environ.get("CORE_MEMORY_STORYLINE_MIN_WORLDLINES", "2")))
    except (TypeError, ValueError):
        return 2


def _min_shared_beads() -> int:
    try:
        return max(1, int(os.environ.get("CORE_MEMORY_STORYLINE_MIN_SHARED_BEADS", "2")))
    except (TypeError, ValueError):
        return 2


def convergence_key(worldline_ids: list[str]) -> str:
    """Stable identity for a convergence group (dedup + supersession key)."""
    digest = hashlib.sha256("|".join(sorted(set(worldline_ids))).encode("utf-8")).hexdigest()
    return f"conv-{digest[:16]}"


def detect_worldline_convergence(root: str | Path) -> list[dict[str, Any]]:
    """Find groups of worldlines that repeatedly intersect.

    Returns one row per convergence group:
    ``{convergence_key, worldline_ids, worldline_labels, kinds, shared_bead_ids,
       statement, confidence, revision_triggers}``

    Threshold: a group qualifies when ≥ ``CORE_MEMORY_STORYLINE_MIN_WORLDLINES``
    distinct worldlines share ≥ ``CORE_MEMORY_STORYLINE_MIN_SHARED_BEADS``
    convergent beads. Kind diversity (claim+entity+goal) raises confidence —
    three entity threads crossing matters less than an entity thread crossing
    a goal arc and a claim chain.
    """
    derived = derive_worldlines(root)
    worldlines = [w for w in (derived.get("worldlines") or []) if len(w.get("bead_ids") or []) >= 2]
    if len(worldlines) < 2:
        return []

    by_id = {w["id"]: w for w in worldlines}
    membership: dict[str, set[str]] = {}
    for w in worldlines:
        for bid in w["bead_ids"]:
            membership.setdefault(bid, set()).add(w["id"])

    # Convergent beads: where at least min_worldlines threads intersect.
    convergent = {bid: wls for bid, wls in membership.items() if len(wls) >= _min_worldlines()}
    if not convergent:
        return []

    # Group worldlines into connected components over shared convergent beads.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for wls in convergent.values():
        ordered = sorted(wls)
        for other in ordered[1:]:
            union(ordered[0], other)

    groups: dict[str, set[str]] = {}
    for wls in convergent.values():
        for wl in wls:
            groups.setdefault(find(wl), set()).add(wl)

    out: list[dict[str, Any]] = []
    for members in groups.values():
        member_ids = sorted(members)
        shared = sorted(
            bid for bid, wls in convergent.items() if wls.intersection(members)
        )
        if len(member_ids) < _min_worldlines() or len(shared) < _min_shared_beads():
            continue
        kinds = sorted({str(by_id[w]["kind"]) for w in member_ids if w in by_id})
        labels = [str(by_id[w]["label"]) for w in member_ids if w in by_id]

        # Confidence: base from group size, boosted by kind diversity, capped.
        confidence = min(0.9, 0.45 + 0.08 * len(member_ids) + 0.12 * (len(kinds) - 1))

        statement = (
            f"{len(member_ids)} continuity threads converge across "
            f"{len(shared)} shared beads: {', '.join(labels[:4])}"
            f"{'…' if len(labels) > 4 else ''}. "
            f"These histories are evolving together."
        )
        revision_triggers = [
            "a member worldline stops intersecting this group in future activity",
            "a contradicting claim lands on a shared bead's subject/slot",
            "a goal worldline in the group resolves against the pattern",
        ]
        out.append({
            "convergence_key": convergence_key(member_ids),
            "worldline_ids": member_ids,
            "worldline_labels": labels,
            "kinds": kinds,
            "shared_bead_ids": shared,
            "statement": statement,
            "confidence": round(confidence, 3),
            "revision_triggers": revision_triggers,
        })

    out.sort(key=lambda r: (-len(r["worldline_ids"]), r["convergence_key"]))
    return out


def enqueue_narrative_candidates(
    root: str | Path,
    *,
    run_id: str | None = None,
    source: str = "dreamer_convergence",
) -> dict[str, Any]:
    """Emit ``narrative_candidate`` rows for new convergence groups.

    Dedup: a group (by convergence_key) is skipped while a pending candidate
    or an active overlay already covers it — re-emission happens only when
    the group's membership changes (new key) or its overlay was superseded
    or the prior candidate rejected.
    """
    from core_memory.runtime.dreamer.candidates import _candidates_path, _read_candidates, _write_candidates  # noqa: PLC0415
    from core_memory.graph.storylines import read_active_overlays  # noqa: PLC0415

    detections = detect_worldline_convergence(root)
    if not detections:
        return {"ok": True, "detected": 0, "enqueued": 0}

    rows = _read_candidates(root)
    blocked_keys: set[str] = set()
    for r in rows:
        if str(r.get("hypothesis_type") or "") != "narrative_candidate":
            continue
        if str(r.get("status") or "") in {"pending", "accepted"}:
            blocked_keys.add(str(r.get("convergence_key") or ""))
    for overlay in read_active_overlays(root):
        blocked_keys.add(str(overlay.get("convergence_key") or ""))

    now = datetime.now(timezone.utc).isoformat()
    enqueued = 0
    for det in detections:
        if det["convergence_key"] in blocked_keys:
            continue
        rows.append({
            "id": f"dc-{uuid.uuid4().hex[:12]}",
            "created_at": now,
            "status": "pending",
            "hypothesis_type": "narrative_candidate",
            "proposal_family": "storyline_overlay",
            "benchmark_tags": ["storyline", "convergence"],
            "rationale": det["statement"],
            "expected_decision_impact": "Accepting writes a storyline overlay (interpretation); the backbone is untouched either way.",
            "statement": det["statement"],
            "convergence_key": det["convergence_key"],
            "supporting_worldline_ids": det["worldline_ids"],
            "supporting_bead_ids": det["shared_bead_ids"],
            "worldline_labels": det["worldline_labels"],
            "kinds": det["kinds"],
            "confidence": det["confidence"],
            "expected_revision_triggers": det["revision_triggers"],
            "novelty": 0.0,
            "grounding": 1.0,
            "run_metadata": {
                "run_id": str(run_id or f"conv-{uuid.uuid4().hex[:8]}"),
                "mode": "suggest",
                "source": source,
            },
        })
        enqueued += 1

    if enqueued:
        _write_candidates(root, rows)
    return {
        "ok": True,
        "detected": len(detections),
        "enqueued": enqueued,
        "path": str(_candidates_path(root)),
    }
