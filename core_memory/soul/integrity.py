"""SOUL integrity checks (PRD: docs/PRD/soul-files.md §8.3, §9.2, §13.5).

Structural integrity is **document maintenance, not identity evolution** (§8.3):
broken references, duplicates, empty/degenerate entries, invalid structure.
Integrity fixes may **auto-apply only when they do not alter identity, goals, or
endorsed meaning** (§8.3 / §9.2 auto governance) — everything else is reported
for human/agent review, never silently changed.

Repairs go through the normal revision store as ``source="integrity_check"``
revisions (append-only; the markdown re-folds), so an integrity action is itself
auditable and reversible like any other SOUL change.

v1 checks:

- ``empty_entry`` — an applied entry whose content is empty/whitespace. Carries
  no identity or endorsed meaning, so removing it is **auto-repairable**.
- ``duplicate_content`` — two entries in one file with identical normalized
  content. Reported only (which one is canonical is a meaning decision).
- ``broken_evidence_reference`` — an entry citing evidence ``bead_id``s absent
  from the bead index. Reported only (dropping cited evidence changes provenance).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core_memory.soul.store import (
    DEFAULT_SUBJECT,
    SOUL_FILES,
    current_soul_entries,
    propose_soul_update,
    soul_history,
)

_REPAIRABLE_CODES = {"empty_entry"}


def _index_bead_ids(root: str | Path) -> set[str]:
    p = Path(root) / ".beads" / "index.json"
    if not p.exists():
        return set()
    try:
        idx = json.loads(p.read_text(encoding="utf-8"))
        beads = idx.get("beads") if isinstance(idx, dict) else None
        return {str(k) for k in beads.keys()} if isinstance(beads, dict) else set()
    except Exception:
        return set()


def _normalize_content(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def soul_integrity_check(root: str | Path, *, subject: str = DEFAULT_SUBJECT) -> dict[str, Any]:
    """Scan a subject's SOUL files for structural issues (read-only).

    Returns ``{ok, subject, issue_count, repairable_count, issues}``; each issue
    carries ``{code, target_file, entry_key, severity, repairable, detail}``.
    """
    bead_ids = _index_bead_ids(root)
    revisions = soul_history(root, subject=subject, limit=1_000_000).get("revisions") or []
    rev_by_id = {str(r.get("id") or ""): r for r in revisions if isinstance(r, dict)}

    issues: list[dict[str, Any]] = []
    for file_name in SOUL_FILES:
        entries = (current_soul_entries(root, file_name=file_name, subject=subject).get("entries") or {})
        seen_content: dict[str, str] = {}
        for key, e in entries.items():
            content = str((e or {}).get("content") or "")
            if not content.strip():
                issues.append({
                    "code": "empty_entry",
                    "target_file": file_name,
                    "entry_key": str(key),
                    "severity": "warning",
                    "repairable": True,
                    "detail": "entry has empty content; safe to remove (no identity/endorsed meaning)",
                })
                continue

            norm = _normalize_content(content)
            if norm in seen_content:
                issues.append({
                    "code": "duplicate_content",
                    "target_file": file_name,
                    "entry_key": str(key),
                    "severity": "info",
                    "repairable": False,
                    "detail": f"identical content to entry '{seen_content[norm]}'",
                })
            else:
                seen_content[norm] = str(key)

            rev = rev_by_id.get(str((e or {}).get("revision_id") or ""))
            evidence = list((rev or {}).get("evidence") or []) if isinstance(rev, dict) else []
            missing = sorted({
                str(ref.get("bead_id"))
                for ref in evidence
                if isinstance(ref, dict) and str(ref.get("bead_id") or "") and str(ref.get("bead_id")) not in bead_ids
            })
            if missing:
                issues.append({
                    "code": "broken_evidence_reference",
                    "target_file": file_name,
                    "entry_key": str(key),
                    "severity": "warning",
                    "repairable": False,
                    "detail": f"cited evidence bead_ids not in index: {missing}",
                    "missing_bead_ids": missing,
                })

    repairable_count = sum(1 for i in issues if i.get("repairable"))
    return {
        "ok": True,
        "subject": subject,
        "issue_count": len(issues),
        "repairable_count": repairable_count,
        "issues": issues,
    }


def soul_integrity_repair(root: str | Path, *, subject: str = DEFAULT_SUBJECT, apply: bool = True) -> dict[str, Any]:
    """Apply only the auto-safe structural repairs (§8.3 / §9.2).

    With ``apply=False`` this is a dry run — every repairable issue is returned
    under ``would_repair`` and nothing is written. Non-repairable issues are
    always left for review.
    """
    check = soul_integrity_check(root, subject=subject)
    repaired: list[dict[str, Any]] = []
    would_repair: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for issue in check.get("issues") or []:
        code = str(issue.get("code") or "")
        if not issue.get("repairable") or code not in _REPAIRABLE_CODES:
            skipped.append(issue)
            continue
        if not apply:
            would_repair.append(issue)
            continue

        if code == "empty_entry":
            out = propose_soul_update(
                root,
                target_file=str(issue.get("target_file") or ""),
                entry_key=str(issue.get("entry_key") or ""),
                op="remove",
                subject=subject,
                source="integrity_check",
                epistemic_status="inferred",
                reason="integrity: removed empty entry (no identity/endorsed meaning)",
                requires_approval=False,
            )
            if out.get("ok"):
                repaired.append({**issue, "revision_id": out.get("revision_id")})
            else:
                skipped.append({**issue, "error": out.get("error")})

    return {
        "ok": True,
        "subject": subject,
        "applied": bool(apply),
        "repaired_count": len(repaired),
        "repaired": repaired,
        "would_repair": would_repair,
        "skipped": skipped,
    }


__all__ = ["soul_integrity_check", "soul_integrity_repair"]
