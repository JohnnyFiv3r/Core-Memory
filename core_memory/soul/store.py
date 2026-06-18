"""SOUL Files — agent-authored self-model store (PRD: docs/PRD/soul-files.md).

SOUL is a maintained *theory of self*, not a memory store. The markdown files are
the human-readable **projection**; the authoritative source of truth is an
append-only stream of ``soul_revision.v1`` records (§4.1). The markdown is
rendered by folding the *applied* revisions.

A revision is an entry-level operation (``upsert``/``remove``) on a section of a
SOUL file — deterministic and falsifiable (history retained), avoiding
unified-diff fragility. SOUL is scoped per ``(root, subject)`` at
``.beads/identity/<subject>/`` (§4.2); the default subject is ``self``.

Governance (§8–§10): a revision is ``proposed`` → ``applied`` (via approve, or
directly when ``requires_approval`` is False) | ``rejected``. Only applied
revisions render. Writes serialize through the store lock (§13.0). SOUL never
mutates beads, claims, or C/B/A — it is a projection layer (§3.4).
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import append_jsonl, store_lock

# A subject directory name must be filesystem-safe, traversal-safe, and stable
# across case-insensitive filesystems. Safe IDs (lowercase alnum + -/_ , no
# leading separator) pass through readably; anything else maps to a disjoint
# hashed namespace ("_h…", which a safe ID can never start with), so distinct
# subjects can never collide into one identity directory.
_SAFE_SUBJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

SOUL_REVISION_SCHEMA = "soul_revision.v1"
DEFAULT_SUBJECT = "self"

SOUL_FILES = ("SOUL.md", "GOALS.md", "TENSIONS.md", "WORLDLINES.md", "IDENTITY.md")
_SOURCES = {"human", "agent", "dreamer", "integrity_check"}
_EPISTEMIC = {"observed", "inferred", "endorsed"}
_OPS = {"upsert", "remove"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_subject(subject: str | None) -> str:
    """Injective directory-safe encoding of a subject id.

    Safe ids pass through unchanged; unsafe ids (emails, domains, uppercase,
    dots, slashes, ``..``) map to a collision-resistant ``_h<hash>`` name, never
    silently merging distinct subjects.
    """
    s = str(subject or "").strip()
    if not s:
        return DEFAULT_SUBJECT
    if _SAFE_SUBJECT_RE.match(s):
        return s
    return "_h" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:24]


def _identity_dir(root: str | Path, subject: str) -> Path:
    return Path(root) / ".beads" / "identity" / _safe_subject(subject)


def _revisions_path(root: str | Path, subject: str) -> Path:
    return _identity_dir(root, subject) / "revisions.jsonl"


def _normalize_target_file(target_file: str) -> str | None:
    t = str(target_file or "").strip()
    for f in SOUL_FILES:
        if t.lower() == f.lower():
            return f
    return None


def _read_revisions(root: str | Path, subject: str) -> list[dict[str, Any]]:
    p = _revisions_path(root, subject)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict) and row.get("schema") == SOUL_REVISION_SCHEMA:
                rows.append(row)
    return rows


def _current_entries(revisions: list[dict[str, Any]], target_file: str) -> "OrderedDict[str, dict[str, Any]]":
    """Fold applied revisions for one file into ordered current entries.

    File order in the jsonl is chronological; later applied upserts overwrite in
    place (keeping original position), removes delete.
    """
    entries: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for r in revisions:
        if r.get("target_file") != target_file or r.get("status") != "applied":
            continue
        key = str(r.get("entry_key") or "")
        if not key:
            continue
        if r.get("op") == "remove":
            entries.pop(key, None)
        else:  # upsert
            entries[key] = {
                "content": str(r.get("content") or ""),
                "epistemic_status": str(r.get("epistemic_status") or "inferred"),
                "source": str(r.get("source") or "agent"),
                "revision_id": str(r.get("id") or ""),
            }
    return entries


def _render_markdown(target_file: str, entries: "OrderedDict[str, dict[str, Any]]") -> str:
    title = target_file[:-3] if target_file.endswith(".md") else target_file
    lines = [f"# {title}", ""]
    if not entries:
        lines.append("_(no entries yet)_")
        lines.append("")
    for key, e in entries.items():
        lines.append(f"## {key}")
        lines.append(f"<!-- {e['epistemic_status']} · source:{e['source']} -->")
        lines.append("")
        lines.append(e["content"].rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_rendered(root: str | Path, subject: str, target_file: str, revisions: list[dict[str, Any]]) -> None:
    d = _identity_dir(root, subject)
    d.mkdir(parents=True, exist_ok=True)
    entries = _current_entries(revisions, target_file)
    (d / target_file).write_text(_render_markdown(target_file, entries), encoding="utf-8")


def propose_soul_update(
    root: str | Path,
    *,
    target_file: str,
    entry_key: str,
    content: str = "",
    op: str = "upsert",
    subject: str = DEFAULT_SUBJECT,
    source: str = "agent",
    epistemic_status: str = "inferred",
    reason: str = "",
    evidence: list[dict[str, Any]] | None = None,
    requires_approval: bool = True,
    semantic_task_refs: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a proposed SOUL revision (soul_revision.v1). When
    ``requires_approval`` is False (auto-eligible governance) it is applied
    immediately; otherwise it waits for approval."""
    tf = _normalize_target_file(target_file)
    if tf is None:
        return {"ok": False, "error": "invalid_target_file", "allowed": list(SOUL_FILES)}
    op_n = str(op or "upsert").strip().lower()
    if op_n not in _OPS:
        return {"ok": False, "error": "invalid_op", "allowed": sorted(_OPS)}
    if not str(entry_key or "").strip():
        return {"ok": False, "error": "missing_entry_key"}
    src = str(source or "agent").strip().lower()
    if src not in _SOURCES:
        return {"ok": False, "error": "invalid_source", "allowed": sorted(_SOURCES)}
    epi = str(epistemic_status or "inferred").strip().lower()
    if epi not in _EPISTEMIC:
        return {"ok": False, "error": "invalid_epistemic_status", "allowed": sorted(_EPISTEMIC)}

    subj = _safe_subject(subject)
    auto = not bool(requires_approval)
    rev = {
        "schema": SOUL_REVISION_SCHEMA,
        "id": f"soul-{uuid.uuid4().hex[:12]}",
        "created_at": _now(),
        "subject": subj,
        "subject_raw": str(subject or DEFAULT_SUBJECT),
        "target_file": tf,
        "op": op_n,
        "entry_key": str(entry_key).strip(),
        "content": str(content or ""),
        "source": src,
        "epistemic_status": epi,
        "reason": str(reason or ""),
        "evidence": [dict(e) for e in (evidence or []) if isinstance(e, dict)],
        "semantic_task_refs": [dict(e) for e in (semantic_task_refs or []) if isinstance(e, dict)],
        "metadata": dict(metadata or {}),
        "requires_approval": bool(requires_approval),
        "status": "applied" if auto else "proposed",
        "approver": "",
        "decided_at": _now() if auto else "",
    }
    with store_lock(Path(root)):
        append_jsonl(_revisions_path(root, subj), rev)
        if auto:
            _write_rendered(root, subj, tf, _read_revisions(root, subj))
    return {"ok": True, "revision_id": rev["id"], "status": rev["status"], "subject": subj, "target_file": tf}


def _decide(root: str | Path, *, subject: str, revision_id: str, decision: str, actor: str, note: str) -> dict[str, Any]:
    subj = _safe_subject(subject)
    rid = str(revision_id)
    with store_lock(Path(root)):
        revisions = _read_revisions(root, subj)
        target = next((r for r in revisions if str(r.get("id")) == rid and str(r.get("status")) == "proposed"), None)
        if target is None:
            return {"ok": False, "error": "proposed_revision_not_found", "revision_id": rid}
        # Append-only: a decision is a new record superseding the proposal. The
        # proposal never folds (only "applied" folds); a prior decision for this
        # id blocks re-deciding.
        if any(str(r.get("supersedes_revision_id") or "") == rid for r in revisions):
            return {"ok": False, "error": "already_decided", "revision_id": rid}
        decided = {
            **target,
            "id": f"soul-{uuid.uuid4().hex[:12]}",
            "created_at": _now(),
            "status": "applied" if decision == "approve" else "rejected",
            "approver": str(actor or ""),
            "decided_at": _now(),
            "decision_note": str(note or ""),
            "supersedes_revision_id": rid,
        }
        append_jsonl(_revisions_path(root, subj), decided)
        if decision == "approve":
            _write_rendered(root, subj, str(decided["target_file"]), revisions + [decided])
    return {"ok": True, "revision_id": decided["id"], "status": decided["status"], "target_file": decided["target_file"]}


def approve_soul_update(root: str | Path, *, revision_id: str, subject: str = DEFAULT_SUBJECT, approver: str = "", note: str = "") -> dict[str, Any]:
    """Approve a proposed revision (approval implies apply)."""
    return _decide(root, subject=subject, revision_id=revision_id, decision="approve", actor=approver, note=note)


def apply_soul_update(
    root: str | Path,
    *,
    revision_id: str,
    subject: str = DEFAULT_SUBJECT,
    applied_by: str = "agent",
    note: str = "",
) -> dict[str, Any]:
    """Auto-governance apply of a proposed revision (§9.2 / §13.2).

    Applies (folds) a *proposed* revision the agent deems auto-eligible — without
    a separate human approval. Guardrails: changes that assert or remove endorsed
    meaning always require human approval, so this refuses (``requires_human_approval``)
    when the revision is ``epistemic_status="endorsed"`` or removes a
    human-authored entry. Use ``approve_soul_update`` for those.
    """
    subj = _safe_subject(subject)
    rid = str(revision_id)
    with store_lock(Path(root)):
        revisions = _read_revisions(root, subj)
        target = next((r for r in revisions if str(r.get("id")) == rid and str(r.get("status")) == "proposed"), None)
        if target is None:
            return {"ok": False, "error": "proposed_revision_not_found", "revision_id": rid}
        if any(str(r.get("supersedes_revision_id") or "") == rid for r in revisions):
            return {"ok": False, "error": "already_decided", "revision_id": rid}

        # §9.2 guardrails: endorsed-meaning changes need a human.
        # 1. The proposal itself asserts endorsed meaning.
        if str(target.get("epistemic_status") or "").strip().lower() == "endorsed":
            return {"ok": False, "error": "requires_human_approval", "reason": "endorsed_meaning", "revision_id": rid}
        # 2. The change would overwrite (upsert) or delete (remove) a *protected*
        #    existing entry — one that is endorsed (incl. human-approved entries
        #    whose stored source stays "agent") or human-authored. Auto-apply must
        #    never destroy endorsed/human meaning regardless of op.
        current = _current_entries(revisions, str(target.get("target_file") or "")).get(str(target.get("entry_key") or ""))
        if current is not None:
            cur_status = str(current.get("epistemic_status") or "").strip().lower()
            cur_source = str(current.get("source") or "").strip().lower()
            if cur_status == "endorsed" or cur_source == "human":
                return {"ok": False, "error": "requires_human_approval", "reason": "protected_entry", "revision_id": rid}

        applied = {
            **target,
            "id": f"soul-{uuid.uuid4().hex[:12]}",
            "created_at": _now(),
            "status": "applied",
            "approver": str(applied_by or "agent"),
            "decided_at": _now(),
            "decision_note": str(note or ""),
            "auto_applied": True,
            "supersedes_revision_id": rid,
        }
        append_jsonl(_revisions_path(root, subj), applied)
        _write_rendered(root, subj, str(applied["target_file"]), revisions + [applied])
    return {"ok": True, "revision_id": applied["id"], "status": "applied", "target_file": applied["target_file"]}


def reject_soul_update(root: str | Path, *, revision_id: str, subject: str = DEFAULT_SUBJECT, reviewer: str = "", reason: str = "") -> dict[str, Any]:
    """Reject a proposed revision — it never folds into the projection."""
    return _decide(root, subject=subject, revision_id=revision_id, decision="reject", actor=reviewer, note=reason)


def read_soul_file(root: str | Path, *, file_name: str, subject: str = DEFAULT_SUBJECT) -> dict[str, Any]:
    tf = _normalize_target_file(file_name)
    if tf is None:
        return {"ok": False, "error": "invalid_target_file", "allowed": list(SOUL_FILES)}
    subj = _safe_subject(subject)
    entries = _current_entries(_read_revisions(root, subj), tf)
    return {"ok": True, "subject": subj, "file_name": tf, "markdown": _render_markdown(tf, entries), "entry_count": len(entries)}


def current_soul_entries(root: str | Path, *, file_name: str, subject: str = DEFAULT_SUBJECT) -> dict[str, Any]:
    """Folded current entries for one SOUL file as structured data.

    Returns ``{ok, subject, file_name, entries: {entry_key: {content,
    epistemic_status, source, revision_id}}}``. Unlike ``read_soul_file`` (which
    renders markdown), this exposes per-entry metadata for analysis surfaces
    (e.g. the Dreamer identity/value research detector).
    """
    tf = _normalize_target_file(file_name)
    if tf is None:
        return {"ok": False, "error": "invalid_target_file", "allowed": list(SOUL_FILES)}
    subj = _safe_subject(subject)
    entries = _current_entries(_read_revisions(root, subj), tf)
    return {"ok": True, "subject": subj, "file_name": tf, "entries": dict(entries)}


def list_soul_files(root: str | Path, *, subject: str = DEFAULT_SUBJECT) -> dict[str, Any]:
    subj = _safe_subject(subject)
    revisions = _read_revisions(root, subj)
    files = []
    for f in SOUL_FILES:
        files.append({"file_name": f, "entry_count": len(_current_entries(revisions, f))})
    return {"ok": True, "subject": subj, "files": files}


def soul_history(root: str | Path, *, subject: str = DEFAULT_SUBJECT, limit: int = 500) -> dict[str, Any]:
    subj = _safe_subject(subject)
    revisions = _read_revisions(root, subj)
    return {"ok": True, "subject": subj, "count": len(revisions), "revisions": revisions[-max(1, int(limit)):]}


def remove_entry_if_unchanged(
    root: str | Path,
    *,
    target_file: str,
    entry_key: str,
    expected_revision_id: str,
    subject: str = DEFAULT_SUBJECT,
    source: str = "integrity_check",
    reason: str = "",
) -> dict[str, Any]:
    """Atomically remove an entry only if it is still the *exact* revision the
    caller observed and still empty.

    The whole compare-and-remove runs under the store lock, so a concurrent
    writer that upserts new content for the same key between a caller's read and
    this call can never have its newer entry deleted: the revision_id (and empty
    check) won't match and the remove is refused.
    """
    tf = _normalize_target_file(target_file)
    if tf is None:
        return {"ok": False, "error": "invalid_target_file", "allowed": list(SOUL_FILES)}
    src = str(source or "integrity_check").strip().lower()
    if src not in _SOURCES:
        return {"ok": False, "error": "invalid_source", "allowed": sorted(_SOURCES)}
    subj = _safe_subject(subject)
    key = str(entry_key or "").strip()
    with store_lock(Path(root)):
        revisions = _read_revisions(root, subj)
        entries = _current_entries(revisions, tf)
        cur = entries.get(key)
        if cur is None:
            return {"ok": False, "error": "entry_absent", "entry_key": key}
        if str(cur.get("revision_id") or "") != str(expected_revision_id or ""):
            return {
                "ok": False,
                "error": "entry_changed",
                "entry_key": key,
                "current_revision_id": str(cur.get("revision_id") or ""),
            }
        if str(cur.get("content") or "").strip():
            return {"ok": False, "error": "entry_not_empty", "entry_key": key}
        rev = {
            "schema": SOUL_REVISION_SCHEMA,
            "id": f"soul-{uuid.uuid4().hex[:12]}",
            "created_at": _now(),
            "subject": subj,
            "subject_raw": str(subject or DEFAULT_SUBJECT),
            "target_file": tf,
            "op": "remove",
            "entry_key": key,
            "content": "",
            "source": src,
            "epistemic_status": "inferred",
            "reason": str(reason or ""),
            "evidence": [],
            "requires_approval": False,
            "status": "applied",
            "approver": "",
            "decided_at": _now(),
            "supersedes_revision_id": str(cur.get("revision_id") or ""),
        }
        append_jsonl(_revisions_path(root, subj), rev)
        _write_rendered(root, subj, tf, _read_revisions(root, subj))
    return {"ok": True, "revision_id": rev["id"], "target_file": tf, "entry_key": key}


__all__ = [
    "SOUL_REVISION_SCHEMA",
    "SOUL_FILES",
    "DEFAULT_SUBJECT",
    "propose_soul_update",
    "approve_soul_update",
    "apply_soul_update",
    "reject_soul_update",
    "read_soul_file",
    "current_soul_entries",
    "remove_entry_if_unchanged",
    "list_soul_files",
    "soul_history",
]
