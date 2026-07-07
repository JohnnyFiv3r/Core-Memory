"""Dreamer V3 — identity and value research (PRD §15).

Dreamer compares the *observed self* (behavior: decisions/outcomes) against the
*endorsed self* (``IDENTITY.md`` entries) to surface identity findings for SOUL
review. It never authors SOUL — it emits candidates that the Dreamer→SOUL bridge
turns into human-approval-gated proposals (§8.2, §10). Values are emergent and
belong in ``IDENTITY.md`` (PRD §15: no separate ``VALUES.md``).

Two grounded, falsifiable finding families:

- ``value_candidate`` — an emergent value *revealed by behavior*: a theme that
  recurs across behavior beads with distributed recurrence (enough beads across
  enough sessions, like goal discovery) yet is **not acknowledged** anywhere in
  ``IDENTITY.md``. Captures "values revealed by behavior" and "unacknowledged
  attractors."
- ``identity_divergence_candidate`` — an **endorsed** ``IDENTITY.md`` value with
  **no behavioral support** in the active corpus: the endorsed self is not
  reflected in behavior (endorsed-identity drift). Captures "endorsed-identity
  drift" and "values contradicted by behavior."

Both route to ``IDENTITY.md`` via the bridge. Candidates are hypotheses; the
decide flow and human approval are the guardrails.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.soul.identity_value_signals import detect_identity_value_findings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue_identity_value_candidates(
    root: str | Path,
    *,
    run_id: str | None = None,
    source: str = "dreamer_identity_value",
    subject: str = "self",
) -> dict[str, Any]:
    """Emit ``value_candidate`` / ``identity_divergence_candidate`` rows. Deduped
    by theme/entry-key while a pending/accepted candidate covers it. Idempotent;
    candidates are hypotheses — Dreamer never writes IDENTITY.md (§10)."""
    from core_memory.runtime.dreamer.candidates import _read_candidates, _write_candidates

    detections = detect_identity_value_findings(root, subject=subject)
    if not detections:
        return {"ok": True, "detected": 0, "enqueued": 0}

    rows = _read_candidates(root)
    blocked_values: set[str] = set()
    blocked_divergence: set[str] = set()
    for r in rows:
        if str(r.get("status") or "") not in {"pending", "accepted"}:
            continue
        ht = str(r.get("hypothesis_type") or "")
        if ht == "value_candidate":
            blocked_values.add(str(r.get("value_theme") or ""))
        elif ht == "identity_divergence_candidate":
            blocked_divergence.add(str(r.get("identity_entry_key") or ""))

    now = _now()
    rid = str(run_id or f"idval-{uuid.uuid4().hex[:8]}")
    enqueued = 0
    for det in detections:
        finding = str(det.get("finding") or "")
        if finding == "value_candidate":
            theme = str(det.get("value_theme") or "")
            if not theme or theme in blocked_values:
                continue
            rows.append({
                "id": f"dc-{uuid.uuid4().hex[:12]}",
                "created_at": now,
                "status": "pending",
                "hypothesis_type": "value_candidate",
                "proposal_family": "identity",
                "benchmark_tags": ["identity", "value"],
                "value_theme": theme,
                "statement": det["statement"],
                "rationale": det["statement"],
                "expected_decision_impact": (
                    "Accepting surfaces an emergent value for human/SOUL endorsement in "
                    "IDENTITY.md; Dreamer never authors identity."
                ),
                "supporting_bead_ids": det.get("supporting_bead_ids") or [],
                "occurrence_count": int(det.get("occurrence_count") or 0),
                "session_count": int(det.get("session_count") or 0),
                "subject": subject,
                "novelty": 0.0,
                "grounding": 1.0,
                "run_metadata": {"run_id": rid, "source": source},
            })
            blocked_values.add(theme)
            enqueued += 1
        elif finding == "identity_divergence_candidate":
            key = str(det.get("identity_entry_key") or "")
            if not key or key in blocked_divergence:
                continue
            rows.append({
                "id": f"dc-{uuid.uuid4().hex[:12]}",
                "created_at": now,
                "status": "pending",
                "hypothesis_type": "identity_divergence_candidate",
                "proposal_family": "identity",
                "benchmark_tags": ["identity", "divergence"],
                "identity_entry_key": key,
                "statement": det["statement"],
                "rationale": det["statement"],
                "expected_decision_impact": (
                    "Accepting flags endorsed-identity drift for SOUL review; Dreamer never "
                    "rewrites identity itself."
                ),
                "supporting_bead_ids": det.get("supporting_bead_ids") or [],
                "source_revision_id": str(det.get("source_revision_id") or ""),
                "subject": subject,
                "novelty": 0.0,
                "grounding": 1.0,
                "run_metadata": {"run_id": rid, "source": source},
            })
            blocked_divergence.add(key)
            enqueued += 1

    if enqueued:
        _write_candidates(root, rows)
    return {"ok": True, "detected": len(detections), "enqueued": enqueued, "run_id": rid}


__all__ = ["detect_identity_value_findings", "enqueue_identity_value_candidates"]
