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

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.soul.store import current_soul_entries

_BEHAVIOR_TYPES = {"decision", "outcome"}
_INACTIVE_BEAD_STATUSES = {"superseded", "archived"}
_STOP_TOKENS = {
    "", "the", "and", "for", "with", "this", "that", "core", "memory",
    "from", "into", "over", "when", "what", "will", "should", "value",
    "values", "goal", "goals", "identity", "self", "about", "their", "they",
    "have", "want", "wants", "than", "then", "them", "your", "ours",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_index(root: str | Path) -> dict[str, Any]:
    p = Path(root) / ".beads" / "index.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return int(default)


def _word_tokens(text: Any) -> set[str]:
    """Single canonical tokenizer used on *both* sides of the acknowledgment
    test: lowercase, split on any non-alphanumeric, keep word tokens of length
    >= 3 that are not stop tokens. Multi-word phrases ("cache invalidation") and
    short technical terms ("api") tokenize identically whether they come from a
    bead's entity/topic list or from free-text IDENTITY.md content — so the
    membership comparison can't false-positive on a phrase/length mismatch."""
    return {
        w for w in re.split(r"[^a-z0-9]+", str(text or "").lower())
        if len(w) >= 3 and w not in _STOP_TOKENS
    }


def _is_active(bead: dict[str, Any]) -> bool:
    return str(bead.get("status") or "").strip().lower() not in _INACTIVE_BEAD_STATUSES \
        and str(bead.get("approval_status") or "").strip().lower() != "rejected"


def _bead_theme_tokens(bead: dict[str, Any]) -> set[str]:
    """Theme word tokens for a bead: entities + topics tokenized by ``_word_tokens``
    (structural tags are excluded — they carry source-system / bead-type noise)."""
    out: set[str] = set()
    for v in list(bead.get("entities") or []) + list(bead.get("topics") or []):
        out |= _word_tokens(v)
    return out


def _identity_tokens(root: str | Path, subject: str) -> set[str]:
    """All word tokens already acknowledged anywhere in ``IDENTITY.md`` (entry
    keys + content), tokenized the *same* way as bead themes, so a value the
    agent already names is never re-proposed."""
    out: set[str] = set()
    entries = (current_soul_entries(root, file_name="IDENTITY.md", subject=subject).get("entries") or {})
    for key, e in entries.items():
        out |= _word_tokens(key)
        out |= _word_tokens(str((e or {}).get("content") or ""))
    return out


def detect_identity_value_findings(root: str | Path, *, subject: str = "self") -> list[dict[str, Any]]:
    """Return identity/value detections comparing behavior against IDENTITY.md."""
    min_occ = max(2, _int_env("CORE_MEMORY_VALUE_RESEARCH_MIN_OCCURRENCES", 4))
    min_sessions = max(2, _int_env("CORE_MEMORY_VALUE_RESEARCH_MIN_SESSIONS", 3))

    index = _read_index(root)
    beads = {str(k): v for k, v in (index.get("beads") or {}).items() if isinstance(v, dict)}
    for bid, b in beads.items():
        b.setdefault("id", bid)

    identity_tokens = _identity_tokens(root, subject)

    # Behavior theme distribution (beads + sessions per token).
    theme_beads: dict[str, set[str]] = {}
    theme_sessions: dict[str, set[str]] = {}
    for bid, b in beads.items():
        if str(b.get("type") or "").strip().lower() not in _BEHAVIOR_TYPES or not _is_active(b):
            continue
        sess = str(b.get("session_id") or "")
        for token in _bead_theme_tokens(b):
            theme_beads.setdefault(token, set()).add(bid)
            theme_sessions.setdefault(token, set()).add(sess)

    out: list[dict[str, Any]] = []

    # (1) value_candidate — emergent value revealed by behavior, absent from IDENTITY.md.
    for token, bead_ids in theme_beads.items():
        if token in identity_tokens:
            continue  # already acknowledged in the self-model
        sessions = theme_sessions.get(token, set())
        if len(bead_ids) < min_occ or len(sessions) < min_sessions:
            continue
        out.append({
            "finding": "value_candidate",
            "value_theme": token,
            "statement": (
                f"Behavior repeatedly centers on '{token}' across {len(sessions)} sessions "
                f"({len(bead_ids)} decisions/outcomes) but it is not acknowledged in IDENTITY.md — "
                "a possible emergent value."
            ),
            "supporting_bead_ids": sorted(bead_ids),
            "occurrence_count": len(bead_ids),
            "session_count": len(sessions),
        })

    # (2) identity_divergence_candidate — endorsed IDENTITY value with no behavioral support.
    identity_entries = (current_soul_entries(root, file_name="IDENTITY.md", subject=subject).get("entries") or {})
    for key, e in identity_entries.items():
        if str((e or {}).get("epistemic_status") or "").strip().lower() != "endorsed":
            continue
        value_tokens = _word_tokens(key) | _word_tokens(str((e or {}).get("content") or ""))
        if not value_tokens:
            continue
        support_beads: set[str] = set()
        for token in value_tokens:
            support_beads |= theme_beads.get(token, set())
        if support_beads:
            continue  # endorsed value is still reflected in behavior
        out.append({
            "finding": "identity_divergence_candidate",
            "identity_entry_key": str(key),
            "statement": (
                f"Endorsed identity '{key}' has no supporting behavior (no decisions/outcomes "
                "reference it) — possible endorsed-identity drift worth review."
            ),
            "supporting_bead_ids": [],
            "source_revision_id": str((e or {}).get("revision_id") or ""),
        })

    out.sort(key=lambda d: (
        0 if d["finding"] == "value_candidate" else 1,
        -int(d.get("session_count") or 0),
        -int(d.get("occurrence_count") or 0),
        str(d.get("value_theme") or d.get("identity_entry_key") or ""),
    ))
    return out


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
