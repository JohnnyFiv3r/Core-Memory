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
import os
from pathlib import Path
from typing import Any, Literal

from core_memory.persistence.dreamer_candidate_store import read_candidates as _read_candidates
from core_memory.persistence.myelination_manifest import read_myelination_manifest
from core_memory.policy.semantic_task_runtime import get_semantic_task_runtime
from core_memory.policy.semantic_task_verifier import verify_semantic_task_output
from core_memory.schema.semantic_tasks import SemanticTaskRequest, TASK_SOUL_PROPOSAL
from core_memory.soul.store import current_soul_entries, propose_soul_update, soul_history

AuthorityTier = Literal["auto_write", "candidate_only", "not_surfaced"]

# Hypothesis types whose findings are inherently contradiction-shaped and must
# ALWAYS route to human review (Decision #4), regardless of confidence. NOTE:
# soul_integrity_check only detects empty/duplicate entries — it has no
# contradiction code — so contradiction signal comes from the candidate family,
# not from integrity checks (PRD-D §4.1/§4.3 assumed otherwise).
_CONTRADICTION_HYPOTHESIS_TYPES = {
    "identity_divergence_candidate",
    "contradiction_pressure_candidate",
}


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return float(default)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}

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


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


def _pruning_flag_from(
    candidate: dict[str, Any],
    *,
    draft: dict[str, Any] | None = None,
    fallback_reason: Any = "",
) -> dict[str, Any] | None:
    """Normalize candidate/task pruning intent into one metadata shape.

    Pruning is advisory review work: it may suggest stale or superseded SOUL
    meaning, but it must never auto-write because removal/supersession changes
    the user's self-model.
    """
    draft = draft if isinstance(draft, dict) else {}
    needs_pruning = (
        _boolish(candidate.get("needs_pruning"))
        or _boolish(candidate.get("pruning_flag"))
        or _boolish(draft.get("needs_pruning"))
        or _boolish(draft.get("pruning_flag"))
    )
    reason = _clean_text(
        draft.get("pruning_reason")
        or candidate.get("pruning_reason")
        or candidate.get("supersession_reason")
        or fallback_reason,
        max_len=400,
    )
    if not needs_pruning and not reason:
        return None
    return {
        "needs_pruning": True,
        "reason": reason or "Dreamer surfaced stale or superseded SOUL meaning.",
    }


def _auto_mode_paused(root: str | Path) -> bool:
    """Resolve the auto-write safety gate (Decision #8).

    Auto-write is only permitted when calibration is healthy. We read PRD-B's
    ``auto_mode_gate`` from the calibration meter; any non-``open`` value (or any
    failure) pauses auto-write. ``SOUL_AUTO_MODE_PAUSED`` is an explicit operator
    kill-switch that forces pause regardless of calibration.
    """
    if _bool_env("SOUL_AUTO_MODE_PAUSED", False):
        return True
    try:
        from core_memory.persistence.calibration import compute_calibration_curve

        gate = str(compute_calibration_curve(root).get("auto_mode_gate") or "paused")
        return gate != "open"
    except Exception:
        # No calibration signal available → safe default is paused.
        return True


def _bead_bonus_lookup(manifest: dict[str, Any]) -> dict[str, float]:
    if not manifest.get("present"):
        return {}
    out: dict[str, float] = {}
    for bead_id, value in dict(manifest.get("bonus_by_bead_id") or {}).items():
        try:
            out[str(bead_id)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def _enrich_findings_with_signals(
    findings: list[dict[str, Any]],
    *,
    bead_bonus: dict[str, float],
    auto_mode_paused: bool,
) -> None:
    """Annotate each eligible finding in place with the gate signals.

    ``judge_prior`` is the candidate's write-time confidence. ``effective_confidence``
    is the Myelination posterior (``clamp(judge_prior + max bead bonus, 0, 1)``),
    computed exactly the way the BFS / calibration meter do it — judge_prior plus
    the manifest bonus, never a flat default. It is ``None`` until a supporting
    bead has manifest history (``min_hits_cleared``). The authority tier is then
    classified from the staged gate.
    """
    for finding in findings:
        cand = finding.get("candidate") or {}
        raw_conf = cand.get("confidence")
        try:
            judge_prior: float | None = (
                max(0.0, min(1.0, float(raw_conf))) if raw_conf is not None else None
            )
        except (TypeError, ValueError):
            judge_prior = None

        supporting = [str(b or "").strip() for b in (cand.get("supporting_bead_ids") or []) if str(b or "").strip()]
        bonuses = [bead_bonus[b] for b in supporting if b in bead_bonus]
        min_hits_cleared = bool(bonuses)
        max_bonus = max(bonuses) if bonuses else 0.0
        effective_confidence: float | None = (
            max(0.0, min(1.0, judge_prior + max_bonus))
            if (min_hits_cleared and judge_prior is not None)
            else None
        )
        contradiction_present = (
            str(finding.get("hypothesis_type") or "") in _CONTRADICTION_HYPOTHESIS_TYPES
        )
        pruning_flag = _pruning_flag_from(cand)
        tier = _classify_confidence(
            judge_prior,
            effective_confidence,
            min_hits_cleared=min_hits_cleared,
            contradiction_present=contradiction_present,
            auto_mode_paused=auto_mode_paused,
        )
        if pruning_flag:
            tier = "candidate_only"
        finding["judge_prior"] = judge_prior
        finding["effective_confidence"] = effective_confidence
        finding["min_hits_cleared"] = min_hits_cleared
        finding["contradiction_present"] = contradiction_present
        finding["pruning_flag"] = pruning_flag
        finding["supporting_bead_count"] = len(supporting)
        finding["authority_tier"] = tier


def _classify_confidence(
    judge_prior: float | None,
    effective_confidence: float | None,
    *,
    min_hits_cleared: bool,
    contradiction_present: bool,
    auto_mode_paused: bool,
) -> AuthorityTier:
    """Staged confidence gate (shared model — identical to PRD-C).

    Cold-start gates on ``judge_prior``; once a bead/edge clears MIN_HITS of
    traversal/reward history, gate on ``effective_confidence`` (the Myelination
    posterior). Contradictions and a paused auto-mode always force human review.
    """
    gate_value = (
        effective_confidence if (min_hits_cleared and effective_confidence is not None) else judge_prior
    )
    if contradiction_present:
        return "candidate_only"
    if auto_mode_paused:
        return "candidate_only"
    if gate_value is None:
        return "candidate_only"
    if gate_value >= _float_env("SOUL_AUTO_WRITE_THRESHOLD", 0.90):
        return "auto_write"
    if gate_value >= _float_env("SOUL_CANDIDATE_THRESHOLD", 0.80):
        return "candidate_only"
    return "not_surfaced"


def _maybe_auto_endorse_goal(root: str | Path, finding: dict[str, Any], *, subject: str) -> dict[str, Any]:
    """Flag-gated Goal Bead ``candidate → endorsed`` escalation (PRD-D §4.4).

    The riskiest auto-action in PRD-D, so it is OFF by default
    (``SOUL_GOAL_AUTO_ENDORSE``). Even when enabled it only fires for a
    ``goal_candidate`` that reached the ``auto_write`` tier with no contradiction
    flag. Best-effort: never breaks the proposal flow.
    """
    if finding.get("hypothesis_type") != "goal_candidate":
        return {"goal_auto_endorse": "not_applicable"}
    if not _bool_env("SOUL_GOAL_AUTO_ENDORSE", False):
        return {"goal_auto_endorse": "disabled"}
    if finding.get("authority_tier") != "auto_write" or finding.get("contradiction_present"):
        return {"goal_auto_endorse": "ineligible"}
    try:
        from core_memory.persistence.goal_lifecycle_v2 import transition_goal_state_for_store
        from core_memory.persistence.store import MemoryStore
        from core_memory.soul.goals import list_goals

        theme = str((finding.get("candidate") or {}).get("goal_theme") or "").strip().lower()
        goals = list_goals(str(root), subject=subject, include_terminal=False).get("goals") or []
        match = next(
            (
                g
                for g in goals
                if str(g.get("state") or "").strip().lower() == "candidate"
                and theme
                and theme in {str(g.get("theme") or "").strip().lower(), str(g.get("title") or "").strip().lower()}
            ),
            None,
        )
        if not match:
            return {"goal_auto_endorse": "no_candidate_goal_match"}
        goal_bead_id = str(match.get("goal_bead_id") or match.get("bead_id") or match.get("id") or "")
        if not goal_bead_id:
            return {"goal_auto_endorse": "no_goal_bead_id"}
        res = transition_goal_state_for_store(
            MemoryStore(root=str(root)),
            goal_bead_id=goal_bead_id,
            to_state="endorsed",
            reason="auto_write_soul_authoring",
            actor="soul_dreamer_bridge",
        )
        return {"goal_auto_endorse": "endorsed" if res.get("ok") else "transition_failed", "goal_bead_id": goal_bead_id}
    except Exception as exc:  # never break authoring on a goal-tie failure
        return {"goal_auto_endorse": "error", "goal_auto_endorse_error": exc.__class__.__name__}


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
        '      "needs_pruning": false,\n'
        '      "pruning_reason": "optional reason when this proposal should remove or supersede stale SOUL meaning",\n'
        '      "review_notes": ["what a reviewer should inspect"],\n'
        '      "evidence_limitations": ["what evidence is missing or weak"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Only reference candidate_ids from the input. Keep content concise, "
        "inferred, and explicitly grounded in the supplied finding.\n"
        "Set \"needs_pruning\": true and give a \"pruning_reason\" when the draft "
        "contradicts or supersedes an existing applied entry, or the finding is "
        "flagged with a contradiction. A flagged draft is never auto-written — it "
        "always routes to human review."
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
                "judge_prior": f.get("judge_prior"),
                "effective_confidence": f.get("effective_confidence"),
                "min_hits_cleared": bool(f.get("min_hits_cleared")),
                "contradiction_present": bool(f.get("contradiction_present")),
                "supporting_bead_count": int(f.get("supporting_bead_count") or 0),
                "needs_pruning": bool((f.get("pruning_flag") or {}).get("needs_pruning")),
                "pruning_reason": str((f.get("pruning_flag") or {}).get("reason") or ""),
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
            "needs_pruning": _boolish(item.get("needs_pruning") or item.get("pruning_flag")),
            "pruning_reason": _clean_text(item.get("pruning_reason") or "", max_len=400),
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
    runtime = get_semantic_task_runtime()
    result = runtime.run(
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

    verification = verify_semantic_task_output(
        root=str(root),
        source_task_type=TASK_SOUL_PROPOSAL,
        source_task_id=str(result.task_id or ""),
        source_receipt_id=str(result.receipt_id or ""),
        output_schema=SOUL_PROPOSAL_OUTPUT_SCHEMA,
        output_json=result.output_json,
        authority_boundary="candidate_only",
        evidence_refs=list(result.evidence_refs or []),
        required_top_level_fields=["proposal_drafts"],
        policy_rubric="SOUL proposal output may draft proposed revisions only; it cannot approve, apply, reject, write rendered SOUL files, or claim endorsed truth.",
        require_semantic_verifier=True,
        runtime=runtime,
    )
    out["verification"] = {
        "ok": bool(verification.get("ok")),
        "status": str(verification.get("status") or ""),
        "decision": str(verification.get("decision") or ""),
        "task_id": str(verification.get("task_id") or ""),
        "receipt_id": str(verification.get("receipt_id") or ""),
        "warnings": list(verification.get("warnings") or []),
        "blocking_errors": list(verification.get("blocking_errors") or []),
        "task_ref": dict(verification.get("task_ref") or {}),
    }
    if not verification.get("ok"):
        out["status"] = "blocked_by_verifier"
        out["drafted"] = 0
        out["error"] = "soul_proposal_verifier_blocked_output"
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
    identity_entries = current_soul_entries(root, file_name="IDENTITY.md", subject=subject).get("entries") or {}

    eligible: list[dict[str, Any]] = []
    skipped = 0
    skipped_stale_divergence = 0
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
        if ht == "identity_divergence_candidate":
            identity_key = str(cand.get("identity_entry_key") or "").strip()
            if not identity_key or identity_key not in identity_entries:
                skipped += 1
                skipped_stale_divergence += 1
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

    # Confidence-gated authority (PRD-D §4.2). Enrich findings with the staged
    # gate signals, then drop anything below the surfacing threshold before it
    # ever reaches the LLM or the store.
    auto_mode_paused = _auto_mode_paused(root)
    bead_bonus = _bead_bonus_lookup(read_myelination_manifest(root))
    _enrich_findings_with_signals(eligible, bead_bonus=bead_bonus, auto_mode_paused=auto_mode_paused)
    surfaced = [f for f in eligible if f.get("authority_tier") != "not_surfaced"]
    skipped += len(eligible) - len(surfaced)

    proposal_task = _run_soul_proposal_task(root, subject=subject, findings=surfaced)
    drafts = proposal_task.get("drafts") if isinstance(proposal_task.get("drafts"), dict) else {}
    task_ref = proposal_task.get("task_ref") if isinstance(proposal_task.get("task_ref"), dict) else {}
    verification = proposal_task.get("verification") if isinstance(proposal_task.get("verification"), dict) else {}
    verifier_ref = verification.get("task_ref") if isinstance(verification.get("task_ref"), dict) else {}

    proposed = 0
    auto_written = 0
    revision_ids: list[str] = []
    for finding in surfaced:
        cand = finding["candidate"]
        ht = str(finding.get("hypothesis_type") or "")
        target_file = str(finding.get("target_file") or "")
        entry_key = str(finding.get("entry_key") or "")
        tier = str(finding.get("authority_tier") or "candidate_only")
        epistemic_status = "inferred"
        draft = drafts.get(str(finding.get("candidate_id") or "")) if isinstance(drafts, dict) else None
        draft = draft if isinstance(draft, dict) else {}
        pruning_flag = _pruning_flag_from(
            cand,
            draft=draft,
            fallback_reason=finding.get("statement") if finding.get("contradiction_present") else "",
        )
        if pruning_flag:
            tier = "candidate_only"
        # Auto-write only for the auto_write tier AND non-endorsed meaning.
        # Endorsed always requires human approval (belt-and-suspenders: the store
        # guardrail in apply_soul_update enforces this too).
        requires_approval = not (tier == "auto_write" and epistemic_status != "endorsed")
        goal_tie = _maybe_auto_endorse_goal(root, finding, subject=subject) if tier == "auto_write" else {}
        content = str(draft.get("content") or finding.get("statement") or "").strip()
        reason = str(draft.get("reason") or f"Dreamer finding ({ht}) surfaced for self-model review.")
        metadata = {
            "dreamer_candidate_id": str(finding.get("candidate_id") or ""),
            "dreamer_hypothesis_type": ht,
            "proposal_task_status": str(proposal_task.get("status") or ""),
            "used_operator_draft": bool(draft.get("content")),
            "operator_review_notes": list(draft.get("review_notes") or []),
            "operator_evidence_limitations": list(draft.get("evidence_limitations") or []),
            # PRD-D §4.2 authority audit trail.
            "authority_tier": tier,
            "judge_prior": finding.get("judge_prior"),
            "effective_confidence": finding.get("effective_confidence"),
            "min_hits_cleared": bool(finding.get("min_hits_cleared")),
            "contradiction_present": bool(finding.get("contradiction_present")),
            "pruning_flag": dict(pruning_flag or {}),
            "auto_mode_paused": auto_mode_paused,
            **goal_tie,
            "operator_verification": {
                "status": str(verification.get("status") or ""),
                "decision": str(verification.get("decision") or ""),
                "warnings": list(verification.get("warnings") or []),
                "blocking_errors": list(verification.get("blocking_errors") or []),
                "task_id": str(verification.get("task_id") or ""),
                "receipt_id": str(verification.get("receipt_id") or ""),
            },
        }
        semantic_refs = [ref for ref in (task_ref, verifier_ref) if ref]

        out = propose_soul_update(
            root,
            target_file=target_file,
            entry_key=entry_key,
            content=content,
            op="upsert",
            subject=subject,
            source="dreamer",
            epistemic_status=epistemic_status,
            reason=reason,
            evidence=list(finding.get("evidence") or []),
            requires_approval=requires_approval,
            semantic_task_refs=semantic_refs,
            metadata=metadata,
        )
        if out.get("ok"):
            proposed += 1
            if not requires_approval:
                auto_written += 1
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
        "auto_written": auto_written,
        "candidate_only": proposed - auto_written,
        "not_surfaced": len(eligible) - len(surfaced),
        "auto_mode_paused": auto_mode_paused,
        "skipped": skipped,
        "skipped_stale_divergence": skipped_stale_divergence,
        "revision_ids": revision_ids,
        "soul_proposal": {
            "ok": bool(proposal_task.get("ok", True)),
            "status": str(proposal_task.get("status") or ""),
            "task_id": str(proposal_task.get("task_id") or ""),
            "receipt_id": str(proposal_task.get("receipt_id") or ""),
            "drafted": int(proposal_task.get("drafted") or 0),
            "authority_boundary": str(proposal_task.get("authority_boundary") or "candidate_only"),
            "verification": {
                "ok": bool(verification.get("ok")) if verification else True,
                "status": str(verification.get("status") or ""),
                "decision": str(verification.get("decision") or ""),
                "task_id": str(verification.get("task_id") or ""),
                "receipt_id": str(verification.get("receipt_id") or ""),
            },
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
