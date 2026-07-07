"""Read-only identity/value signal detection for SOUL continuity summaries.

These helpers compare observed behavior beads against endorsed ``IDENTITY.md``
entries. They do not enqueue Dreamer candidates or mutate SOUL state.
"""
from __future__ import annotations

import json
import os
import re
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
    """Single canonical tokenizer used on both sides of acknowledgment checks."""
    return {
        w for w in re.split(r"[^a-z0-9]+", str(text or "").lower())
        if len(w) >= 3 and w not in _STOP_TOKENS
    }


def _is_active(bead: dict[str, Any]) -> bool:
    return str(bead.get("status") or "").strip().lower() not in _INACTIVE_BEAD_STATUSES \
        and str(bead.get("approval_status") or "").strip().lower() != "rejected"


def _bead_theme_tokens(bead: dict[str, Any]) -> set[str]:
    """Theme word tokens for a bead from entities and topics."""
    out: set[str] = set()
    for v in list(bead.get("entities") or []) + list(bead.get("topics") or []):
        out |= _word_tokens(v)
    return out


def _identity_tokens(root: str | Path, subject: str) -> set[str]:
    """All word tokens already acknowledged anywhere in ``IDENTITY.md``."""
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

    # Behavior theme distribution: beads and sessions per token.
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

    for token, bead_ids in theme_beads.items():
        if token in identity_tokens:
            continue
        sessions = theme_sessions.get(token, set())
        if len(bead_ids) < min_occ or len(sessions) < min_sessions:
            continue
        out.append({
            "finding": "value_candidate",
            "value_theme": token,
            "statement": (
                f"Behavior repeatedly centers on '{token}' across {len(sessions)} sessions "
                f"({len(bead_ids)} decisions/outcomes) but it is not acknowledged in IDENTITY.md \u2014 "
                "a possible emergent value."
            ),
            "supporting_bead_ids": sorted(bead_ids),
            "occurrence_count": len(bead_ids),
            "session_count": len(sessions),
        })

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
            continue
        out.append({
            "finding": "identity_divergence_candidate",
            "identity_entry_key": str(key),
            "statement": (
                f"Endorsed identity '{key}' has no supporting behavior (no decisions/outcomes "
                "reference it) \u2014 possible endorsed-identity drift worth review."
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


__all__ = ["detect_identity_value_findings"]
