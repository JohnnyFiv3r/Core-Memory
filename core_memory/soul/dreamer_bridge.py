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

import json
from pathlib import Path
from typing import Any

from core_memory.runtime.dreamer.candidates import _read_candidates
from core_memory.runtime.semantic_tasks import SemanticTaskRequest, get_semantic_task_runtime
from core_memory.runtime.semantic_tasks.contracts import TASK_SOUL_PROPOSAL
from core_memory.soul.store import propose_soul_update, soul_history

SOUL_PROPOSAL_CONTRACT = "memory.soul_proposal.v1"
SOUL_PROPOSAL_PROMPT_VERSION = "soul_proposal.v1"
SOUL_PROPOSAL_RUBRIC_VERSION = "soul_proposal_candidate_only.v1"
SOUL_PROPOSAL_OUTPUT_SCHEMA = SOUL_PROPOSAL_CONTRACT

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


def _clean_text(value: Any, *, max_len: int = 1200) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > max_len:
        return text[: max(0, max_len - 3)].rstrip() + "..."
    return text


def _proposal_prompt() -> str:
    return (
        "You are the Core Memory SOUL proposal assistant. Draft review-ready "
        "SOUL revision text from Dreamer findings. Return JSON only.\n\n"
        "Authority boundary: candidate_only. You may draft or clarify proposed "
        "SOUL entries, but you must not approve, apply, reject, or claim to "
        "modify SOUL files. Every output remains human/governance review work.\n\n"
        "Return this shape:\n"
        "{\n"
        '  "contract": "memory.soul_proposal.v1",\n'
        '  "subject": "self",\n'
        '  "proposal_drafts": [\n'
        "    {\n"
        '      "candidate_id": "dc-...",\n'
        '      "target_file": "GOALS.md",\n'
        '      "entry_key": "goal:example",\n'
        '      "content": "concise proposed SOUL entry",\n'
        '      "reason": "why this should be reviewed",\n'
        '      "review_notes": ["what a reviewer should inspect"],\n'
        '      "evidence_limitations": ["what evidence is missing or weak"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Only reference candidate_ids from the input. Keep content concise, "
        "inferred, and explicitly grounded in the supplied finding."
    )


def _proposal_payload(findings: list[dict[str, Any]], *, subject: str) -> dict[str, Any]:
    return {
        "contract": SOUL_PROPOSAL_CONTRACT,
        "subject": str(subject or "self"),
        "finding_count": len(findings),
        "findings": [
            {
                "candidate_id": str(f.get("candidate_id") or ""),
                "hypothesis_type": str(f.get("hypothesis_type") or ""),
                "target_file": str(f.get("target_file") or ""),
                "entry_key": str(f.get("entry_key") or ""),
                "statement": _clean_text(f.get("statement") or "", max_len=900),
                "evidence": list(f.get("evidence") or []),
            }
            for f in findings
        ],
    }


def _normalize_drafts(
    payload: dict[str, Any],
    *,
    eligible_keys: set[tuple[str, str, str]],
) -> dict[str, dict[str, Any]]:
    raw = payload.get("proposal_drafts")
    if not isinstance(raw, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("candidate_id") or "").strip()
        target_file = str(item.get("target_file") or "").strip()
        entry_key = str(item.get("entry_key") or "").strip()
        content = _clean_text(item.get("content") or "", max_len=1400)
        if not cid or not content:
            continue
        if (cid, target_file, entry_key) not in eligible_keys:
            continue
        out[cid] = {
            "target_file": target_file,
            "entry_key": entry_key,
            "content": content,
            "reason": _clean_text(item.get("reason") or "", max_len=700),
            "review_notes": [
                _clean_text(note, max_len=240)
                for note in list(item.get("review_notes") or [])
                if _clean_text(note, max_len=240)
            ][:6],
            "evidence_limitations": [
                _clean_text(note, max_len=240)
                for note in list(item.get("evidence_limitations") or [])
                if _clean_text(note, max_len=240)
            ][:6],
        }
    return out


def _run_soul_proposal_task(
    root: str | Path,
    *,
    subject: str,
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    if not findings:
        return {"ok": True, "status": "skipped", "reason": "no_eligible_findings", "drafts": {}}

    payload = _proposal_payload(findings, subject=subject)
    result = get_semantic_task_runtime().run(
        SemanticTaskRequest(
            root=str(root),
            task_type=TASK_SOUL_PROPOSAL,
            prompt=_proposal_prompt() + "\n\nContext JSON:\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True),
            payload=payload,
            idempotency_key=f"soul_proposal:{subject}:{','.join(str(f.get('candidate_id') or '') for f in findings)}",
            prompt_version=SOUL_PROPOSAL_PROMPT_VERSION,
            rubric_version=SOUL_PROPOSAL_RUBRIC_VERSION,
            output_schema=SOUL_PROPOSAL_OUTPUT_SCHEMA,
            max_tokens=1600,
            temperature=0.1,
            json_mode=True,
            fallback_mode="deterministic_soul_bridge",
            authority_boundary="candidate_only",
            evidence_refs=[
                {"type": "dreamer_candidate", "candidate_id": str(f.get("candidate_id") or "")}
                for f in findings
                if str(f.get("candidate_id") or "")
            ],
            metadata={
                "subject": str(subject or "self"),
                "candidate_ids": [str(f.get("candidate_id") or "") for f in findings],
                "finding_count": len(findings),
            },
        )
    )

    task_ref = {
        "task_type": TASK_SOUL_PROPOSAL,
        "task_id": str(result.task_id or ""),
        "receipt_id": str(result.receipt_id or ""),
        "role": "soul_proposal_draft",
    }
    out: dict[str, Any] = {
        "ok": bool(result.ok),
        "status": str(result.status or ""),
        "task_id": str(result.task_id or ""),
        "receipt_id": str(result.receipt_id or ""),
        "authority_boundary": str(result.authority_boundary or "candidate_only"),
        "task_ref": task_ref if task_ref["task_id"] or task_ref["receipt_id"] else {},
        "drafts": {},
    }
    if result.error:
        out["error"] = result.error
    if not result.ok or not isinstance(result.output_json, dict):
        if result.ok:
            out["status"] = "succeeded_unparsed"
        return out

    eligible_keys = {
        (str(f.get("candidate_id") or ""), str(f.get("target_file") or ""), str(f.get("entry_key") or ""))
        for f in findings
    }
    drafts = _normalize_drafts(result.output_json, eligible_keys=eligible_keys)
    out["drafts"] = drafts
    out["drafted"] = len(drafts)
    out["contract"] = str(result.output_json.get("contract") or SOUL_PROPOSAL_CONTRACT)
    return out


def _covered_keys(root: str | Path, subject: str) -> set[tuple[str, str]]:
    """Existing SOUL coverage by stable key, regardless of source. A prior
    Dreamer revision (proposed/applied/rejected) means a finding is already in
    review; a human/agent revision means the entry is authoritatively owned.
    Either way a new Dreamer proposal for that ``(target_file, entry_key)`` is
    skipped, so callers should treat covered keys as not-eligible."""
    history = soul_history(root, subject=subject, limit=100000)
    return {
        (str(rev.get("target_file") or ""), str(rev.get("entry_key") or ""))
        for rev in (history.get("revisions") or [])
    }


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
    covered = _covered_keys(root, subject)

    eligible: list[dict[str, Any]] = []
    skipped = 0
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        if str(cand.get("status") or "").strip().lower() != "pending":
            continue
        # Subject scoping: a candidate explicitly tagged for another subject must
        # never bridge into this subject's SOUL (identity/value findings are
        # subject-scoped). Subjectless candidates (tension/goal/decay) bridge
        # under whatever subject is requested, preserving prior behavior.
        cand_subject = str(cand.get("subject") or "").strip()
        if cand_subject and cand_subject != subject:
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

        eligible.append(
            {
                "candidate": cand,
                "candidate_id": str(cand.get("id") or ""),
                "hypothesis_type": ht,
                "target_file": target_file,
                "entry_key": entry_key,
                "statement": content,
                "evidence": _evidence_from(cand),
            }
        )
        if len(eligible) >= max(1, int(limit)):
            break

    proposal_task = _run_soul_proposal_task(root, subject=subject, findings=eligible)
    drafts = proposal_task.get("drafts") if isinstance(proposal_task.get("drafts"), dict) else {}
    task_ref = proposal_task.get("task_ref") if isinstance(proposal_task.get("task_ref"), dict) else {}

    proposed = 0
    revision_ids: list[str] = []
    for finding in eligible:
        cand = finding["candidate"]
        ht = str(finding.get("hypothesis_type") or "")
        target_file = str(finding.get("target_file") or "")
        entry_key = str(finding.get("entry_key") or "")
        draft = drafts.get(str(finding.get("candidate_id") or "")) if isinstance(drafts, dict) else None
        draft = draft if isinstance(draft, dict) else {}
        content = str(draft.get("content") or finding.get("statement") or "").strip()
        reason = str(draft.get("reason") or f"Dreamer finding ({ht}) surfaced for self-model review.")
        metadata = {
            "dreamer_candidate_id": str(finding.get("candidate_id") or ""),
            "dreamer_hypothesis_type": ht,
            "proposal_task_status": str(proposal_task.get("status") or ""),
            "used_operator_draft": bool(draft.get("content")),
            "operator_review_notes": list(draft.get("review_notes") or []),
            "operator_evidence_limitations": list(draft.get("evidence_limitations") or []),
        }
        semantic_refs = [task_ref] if task_ref else []

        out = propose_soul_update(
            root,
            target_file=target_file,
            entry_key=entry_key,
            content=content,
            op="upsert",
            subject=subject,
            source="dreamer",
            epistemic_status="inferred",
            reason=reason,
            evidence=list(finding.get("evidence") or []),
            requires_approval=True,
            semantic_task_refs=semantic_refs,
            metadata=metadata,
        )
        if out.get("ok"):
            proposed += 1
            covered.add((target_file, entry_key))
            rid = str(out.get("revision_id") or "")
            if rid:
                revision_ids.append(rid)
        else:
            skipped += 1

    return {
        "ok": True,
        "subject": subject,
        "proposed": proposed,
        "skipped": skipped,
        "revision_ids": revision_ids,
        "soul_proposal": {
            "ok": bool(proposal_task.get("ok", True)),
            "status": str(proposal_task.get("status") or ""),
            "task_id": str(proposal_task.get("task_id") or ""),
            "receipt_id": str(proposal_task.get("receipt_id") or ""),
            "drafted": int(proposal_task.get("drafted") or 0),
            "authority_boundary": str(proposal_task.get("authority_boundary") or "candidate_only"),
            **({"error": str(proposal_task.get("error"))} if proposal_task.get("error") else {}),
        },
    }


def dreamer_soul_findings(root: str | Path, *, subject: str = "self", limit: int = 200) -> dict[str, Any]:
    """The Dreamer findings eligible to become SOUL proposals (§13.4 findings).

    Read-only view of the pending Dreamer candidates the bridge would route into
    this subject's SOUL, each annotated with its target file and entry key —
    before any proposal is created. Findings already covered in SOUL history
    (proposed/applied/rejected, or authored by another author) are excluded so
    this matches exactly what a subsequent ``propose-updates`` would create — no
    stale review work.
    """
    covered = _covered_keys(root, subject)
    findings: list[dict[str, Any]] = []
    for cand in _read_candidates(root):
        if not isinstance(cand, dict):
            continue
        if str(cand.get("status") or "").strip().lower() != "pending":
            continue
        cand_subject = str(cand.get("subject") or "").strip()
        if cand_subject and cand_subject != subject:
            continue
        ht = str(cand.get("hypothesis_type") or "").strip()
        spec = _BRIDGE_MAP.get(ht)
        entry_key = _finding_entry_key(cand)
        if spec is None or not entry_key:
            continue
        if (spec[0], entry_key) in covered:
            continue
        findings.append({
            "candidate_id": str(cand.get("id") or ""),
            "hypothesis_type": ht,
            "target_file": spec[0],
            "entry_key": entry_key,
            "statement": str(cand.get("statement") or cand.get("rationale") or ""),
        })
        if len(findings) >= max(1, int(limit)):
            break
    return {"ok": True, "subject": subject, "count": len(findings), "findings": findings}


def dreamer_soul_review(root: str | Path, *, subject: str = "self", limit: int = 200) -> dict[str, Any]:
    """The pending Dreamer-sourced SOUL proposals awaiting human review (§13.4
    run-review). Read-only surface over the proposed revisions the bridge created."""
    from core_memory.soul.store import soul_history

    history = soul_history(root, subject=subject, limit=1_000_000).get("revisions") or []
    # Append-only: a decision supersedes the proposal but the original record
    # keeps status="proposed". Exclude any proposal a later record decided.
    decided = {str(r.get("supersedes_revision_id") or "") for r in history if r.get("supersedes_revision_id")}
    pending = [
        {
            "revision_id": str(r.get("id") or ""),
            "target_file": str(r.get("target_file") or ""),
            "entry_key": str(r.get("entry_key") or ""),
            "op": str(r.get("op") or ""),
            "content": str(r.get("content") or ""),
            "reason": str(r.get("reason") or ""),
            "created_at": str(r.get("created_at") or ""),
        }
        for r in history
        if str(r.get("source") or "").strip().lower() == "dreamer"
        and str(r.get("status") or "").strip().lower() == "proposed"
        and str(r.get("id") or "") not in decided
    ]
    return {"ok": True, "subject": subject, "count": len(pending[: max(1, int(limit))]),
            "proposals": pending[: max(1, int(limit))]}


__all__ = ["propose_soul_from_dreamer", "dreamer_soul_findings", "dreamer_soul_review"]
