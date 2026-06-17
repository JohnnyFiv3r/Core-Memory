from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.association.quarantine import write_quarantine
from core_memory.llm_client import chat_complete
from core_memory.persistence import events
from core_memory.persistence.io_utils import append_jsonl, store_lock
from core_memory.persistence.store import MemoryStore
from core_memory.persistence.store_relationship_ops import _mirror_association_to_graph
from core_memory.policy.association_contract import assoc_dedupe_key
from core_memory.policy.association_inference_v21 import (
    INFERENCE_MODE_STRICT,
    validate_and_normalize_inference_payload,
)
from core_memory.provider_config import resolve_chat_config
from core_memory.retrieval.lifecycle import mark_trace_dirty
from core_memory.schema.normalization import INFERENCE_CANONICAL_RELATION_TYPES, normalize_relation_type


ASSOCIATION_RUNS_SCHEMA = "core_memory.association_runs.v1"
ASSOCIATION_CANDIDATES_SCHEMA = "core_memory.association_candidates.v1"
ASSOCIATION_JUDGE_DECISIONS_SCHEMA = "core_memory.association_judge_decisions.v1"
ASSOCIATION_JUDGE_CONTRACT = "memory.association_judge.v1"
POLICY_VERSION = "bead_association.v1"
JUDGE_PROMPT_VERSION = "association_judge.v1"
JUDGE_RUBRIC_VERSION = "association_truth.v1"
CANDIDATE_GENERATION_VERSION = "association_candidates.v1"
DEFAULT_MAX_CANDIDATES = 40
ALLOWED_TRIGGERS = {
    "bead_committed",
    "pre_commit",
    "post_commit",
    "typed_ingest",
    "session_flush",
    "operator",
    "periodic_transcript_push",
}
INCOMPLETE_STATES = {"deferred", "pending_judge", "judge_failed", "quarantined", "failed"}
JUDGE_ACTIONS = {"accept", "reject", "modify", "invert", "replace", "add", "no_link"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _clean_mode(value: Any, *, default: str = "deterministic") -> str:
    mode = _clean_str(value).lower()
    allowed = {"deterministic", "hybrid", "llm", "model"}
    return mode if mode in allowed else default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _bead_set_signature(bead_ids: list[str]) -> str:
    normalized = sorted({_clean_str(x) for x in bead_ids if _clean_str(x)})
    material = "\n".join(normalized)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _json_object(text: str) -> dict[str, Any] | None:
    raw = _clean_str(text)
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _chat_config_with_model(*env_names: str):
    cfg = resolve_chat_config()
    for name in env_names:
        model = _clean_str(os.environ.get(name))
        if model:
            return replace(cfg, model=model)
    return cfg


def _candidate_generation_mode() -> str:
    return _clean_mode(os.environ.get("CORE_MEMORY_ASSOCIATION_CANDIDATE_MODE"), default="deterministic")


def _graph_revision(index: dict[str, Any]) -> str:
    beads = index.get("beads") or {}
    assocs = index.get("associations") or []
    material = {
        "beads": sorted(str(k) for k in beads.keys()),
        "associations": sorted(str((a or {}).get("id") or "") for a in assocs if isinstance(a, dict)),
        "total_beads": len(beads),
        "total_associations": len(assocs),
    }
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _events_dir(root: str | Path) -> Path:
    path = Path(root) / ".beads" / "events"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _runs_path(root: str | Path) -> Path:
    return _events_dir(root) / "association-runs.jsonl"


def _candidates_path(root: str | Path) -> Path:
    return _events_dir(root) / "association-candidates.jsonl"


def _judge_decisions_path(root: str | Path) -> Path:
    return _events_dir(root) / "association-judge-decisions.jsonl"


def _load_index(root: str | Path) -> dict[str, Any]:
    path = Path(root) / ".beads" / "index.json"
    if not path.exists():
        return {"beads": {}, "associations": [], "stats": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"beads": {}, "associations": [], "stats": {}}
    if not isinstance(data, dict):
        return {"beads": {}, "associations": [], "stats": {}}
    data.setdefault("beads", {})
    data.setdefault("associations", [])
    data.setdefault("stats", {})
    return data


def _append_run_record(root: str | Path, record: dict[str, Any]) -> None:
    row = {
        "schema": ASSOCIATION_RUNS_SCHEMA,
        "recorded_at": _now(),
        **dict(record or {}),
    }
    append_jsonl(_runs_path(root), row)


def _append_candidate_record(root: str | Path, record: dict[str, Any]) -> None:
    row = {
        "schema": ASSOCIATION_CANDIDATES_SCHEMA,
        "recorded_at": _now(),
        **dict(record or {}),
    }
    append_jsonl(_candidates_path(root), row)


def _append_judge_decision_record(root: str | Path, record: dict[str, Any]) -> None:
    row = {
        "schema": ASSOCIATION_JUDGE_DECISIONS_SCHEMA,
        "recorded_at": _now(),
        **dict(record or {}),
    }
    append_jsonl(_judge_decisions_path(root), row)


def _iter_run_records(root: str | Path) -> list[dict[str, Any]]:
    path = _runs_path(root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def get_association_run(root: str | Path, run_id: str) -> dict[str, Any]:
    target = _clean_str(run_id)
    latest: dict[str, Any] | None = None
    for row in _iter_run_records(root):
        if _clean_str(row.get("run_id")) == target:
            latest = row
    if latest is None:
        return {"ok": False, "error": "association_run_not_found", "run_id": target}
    return {"ok": True, "run": dict(latest)}


def latest_association_coverage(root: str | Path, bead_id: str) -> dict[str, Any]:
    target = _clean_str(bead_id)
    latest: dict[str, Any] | None = None
    latest_state = ""
    for row in _iter_run_records(root):
        states = row.get("association_state_by_bead") or {}
        if not isinstance(states, dict) or target not in states:
            continue
        latest = row
        latest_state = _clean_str(states.get(target))
    if latest is None:
        return {"state": "unknown", "bead_id": target}
    return {
        "state": latest_state or "unknown",
        "bead_id": target,
        "run_id": _clean_str(latest.get("run_id")),
        "trigger": _clean_str(latest.get("trigger")),
        "status": _clean_str(latest.get("status")),
        "recorded_at": _clean_str(latest.get("recorded_at")),
        "appended": int((latest.get("counts") or {}).get("appended") or 0),
        "deduped": int((latest.get("counts") or {}).get("deduped") or 0),
        "quarantined": int((latest.get("counts") or {}).get("quarantined") or 0),
    }


def _normalize_trigger(trigger: str | None) -> str:
    value = _clean_str(trigger).lower() or "operator"
    return value if value in ALLOWED_TRIGGERS else "operator"


def _resolve_session_bead_ids(index: dict[str, Any], session_id: str | None) -> list[str]:
    sid = _clean_str(session_id)
    if not sid:
        return []
    rows = []
    for bid, bead in (index.get("beads") or {}).items():
        if _clean_str((bead or {}).get("session_id")) != sid:
            continue
        if not _coverage_eligible(bead):
            continue
        rows.append((_clean_str((bead or {}).get("created_at")), _clean_str(bid)))
    rows.sort()
    return [bid for _created, bid in rows if bid]


def _coverage_eligible(bead: Any) -> bool:
    if not isinstance(bead, dict):
        return False
    if not bool(bead.get("retrieval_eligible", True)):
        return False
    typ = _clean_str(bead.get("type")).lower()
    tags = {_clean_str(x).lower() for x in _as_list(bead.get("tags"))}
    if typ in {"process_flush", "session_start", "checkpoint", "session_end"}:
        return False
    if "process_flush" in tags or "system_checkpoint" in tags:
        return False
    return bool(_clean_str(bead.get("id")))


def _doc_ids(bead: dict[str, Any]) -> set[str]:
    out = {
        _clean_str(bead.get("document_id")),
        _clean_str(bead.get("ragie_document_id")),
        _clean_str(bead.get("raw_source_object_id")),
    }
    return {x for x in out if x}


def _has_section_scope(bead: dict[str, Any]) -> bool:
    return bool([x for x in _as_list(bead.get("section_refs")) if x])


def _same_source(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_source = _clean_str(left.get("source_id"))
    right_source = _clean_str(right.get("source_id"))
    return not left_source or not right_source or left_source == right_source


def _reference_matches_bead(ref: str, bead: dict[str, Any]) -> bool:
    text = _clean_str(ref)
    if not text:
        return False
    if text == _clean_str(bead.get("id")):
        return True
    if text.startswith("bead:") and text.split(":", 1)[1] == _clean_str(bead.get("id")):
        return True
    typ = _clean_str(bead.get("type"))
    suffixes = {
        _clean_str(bead.get("source_record_id")),
        _clean_str(bead.get("business_object_id")),
        _clean_str(bead.get("source_event_id")),
        _clean_str(bead.get("core_memory_unifying_id")),
        _clean_str(bead.get("document_id")),
        _clean_str(bead.get("transcript_id")),
        _clean_str(bead.get("conversation_id")),
    }
    suffixes = {x for x in suffixes if x}
    if ":" in text:
        prefix, suffix = text.split(":", 1)
        return prefix == typ and suffix in suffixes
    return text in suffixes


def _resolve_reference_ids(index: dict[str, Any], refs: list[Any]) -> list[str]:
    out: list[str] = []
    for ref in refs:
        text = _clean_str(ref)
        if not text:
            continue
        for bid, bead in (index.get("beads") or {}).items():
            if isinstance(bead, dict) and _reference_matches_bead(text, bead):
                bid_s = _clean_str(bid)
                if bid_s and bid_s not in out:
                    out.append(bid_s)
    return out


def _candidate_id(row: dict[str, Any]) -> str:
    material = json.dumps(
        {
            "source": _clean_str(row.get("source_bead")),
            "target": _clean_str(row.get("target_bead")),
            "relationship": _clean_str(row.get("proposed_relationship")),
            "reason_code": _clean_str(row.get("reason_code")),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "cand-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _candidate(
    source: str,
    target: str,
    relationship: str,
    *,
    reason_text: str,
    reason_code: str,
    confidence: float = 0.95,
    candidate_class: str = "system_structural_hint",
    evidence_refs: list[Any] | None = None,
) -> dict[str, Any]:
    row = {
        "source_bead": source,
        "target_bead": target,
        "proposed_relationship": relationship,
        "proposed_direction": "source_to_target",
        "candidate_class": candidate_class,
        "reason_code": reason_code,
        "system_rationale": reason_text,
        "confidence_prior": float(confidence),
        "evidence_bead_ids": [source, target],
        "evidence_refs": list(evidence_refs or []),
        "requires_judge": True,
    }
    row["candidate_id"] = _candidate_id(row)
    return row


def _term_values(value: Any) -> set[str]:
    out: set[str] = set()
    for item in _as_list(value):
        if isinstance(item, dict):
            for nested in item.values():
                out.update(_term_values(nested))
            continue
        text = _clean_str(item).lower()
        if not text:
            continue
        if len(text) <= 96:
            out.add(text)
        for token in text.replace("/", " ").replace("_", " ").replace("-", " ").split():
            token = token.strip(".,:;!?()[]{}")
            if len(token) >= 3:
                out.add(token)
    return out


def _association_terms(bead: dict[str, Any]) -> set[str]:
    fields = (
        "entities", "entity_refs", "topics", "tags", "attribute_tags",
        "business_object_type", "business_object_id", "source_record_id",
        "source_table", "metric_name", "document_id", "ragie_document_id",
        "transcript_id", "conversation_id", "assertion_subject",
        "assertion_value", "core_memory_unifying_id",
    )
    terms: set[str] = set()
    for field in fields:
        terms.update(_term_values(bead.get(field)))
    return {term for term in terms if term not in {"external_evidence", "structured_observation", "operational_event"}}


def _add_candidate(candidates: list[str], candidate_id: str, *, self_id: str, beads: dict[str, Any]) -> None:
    value = _clean_str(candidate_id)
    if not value or value == self_id or value not in beads or value in candidates:
        return
    candidates.append(value)


def _candidate_ids_for_bead(
    index: dict[str, Any],
    bead: dict[str, Any],
    *,
    explicit_candidate_ids: list[str],
    max_candidates: int,
) -> list[str]:
    beads = index.get("beads") or {}
    bead_id = _clean_str(bead.get("id"))
    candidates: list[str] = []
    for cid in explicit_candidate_ids:
        _add_candidate(candidates, cid, self_id=bead_id, beads=beads)

    for cid in _as_list(bead.get("supersedes")):
        _add_candidate(candidates, _clean_str(cid), self_id=bead_id, beads=beads)
    for cid in _as_list(bead.get("derived_from_bead_ids")):
        _add_candidate(candidates, _clean_str(cid), self_id=bead_id, beads=beads)
    for cid in _resolve_reference_ids(index, _as_list(bead.get("derived_from"))):
        _add_candidate(candidates, cid, self_id=bead_id, beads=beads)
    for key in ("prev_bead_id", "linked_bead_id", "blocking_bead_id", "revises_bead_id"):
        _add_candidate(candidates, _clean_str(bead.get(key)), self_id=bead_id, beads=beads)
    for cid in _as_list(bead.get("supports_bead_ids")):
        _add_candidate(candidates, _clean_str(cid), self_id=bead_id, beads=beads)

    unifying_id = _clean_str(bead.get("core_memory_unifying_id"))
    doc_ids = _doc_ids(bead)
    source_record_id = _clean_str(bead.get("source_record_id") or bead.get("business_object_id"))
    transcript_id = _clean_str(bead.get("transcript_id") or bead.get("conversation_id"))
    same_session: list[tuple[str, str]] = []
    source_terms = _association_terms(bead)
    semantic_pool: list[tuple[int, str, str]] = []
    for other_id, other in beads.items():
        if not isinstance(other, dict):
            continue
        other_id_s = _clean_str(other_id)
        if not other_id_s or other_id_s == bead_id:
            continue
        if unifying_id and _clean_str(other.get("core_memory_unifying_id")) == unifying_id:
            _add_candidate(candidates, other_id_s, self_id=bead_id, beads=beads)
        if doc_ids and doc_ids.intersection(_doc_ids(other)) and _same_source(bead, other):
            _add_candidate(candidates, other_id_s, self_id=bead_id, beads=beads)
        if source_record_id and _same_source(bead, other):
            other_record = _clean_str(other.get("source_record_id") or other.get("business_object_id"))
            if other_record == source_record_id:
                _add_candidate(candidates, other_id_s, self_id=bead_id, beads=beads)
        if transcript_id and _same_source(bead, other):
            other_transcript = _clean_str(other.get("transcript_id") or other.get("conversation_id"))
            if other_transcript == transcript_id:
                _add_candidate(candidates, other_id_s, self_id=bead_id, beads=beads)
        if _clean_str(other.get("session_id")) == _clean_str(bead.get("session_id")):
            same_session.append((_clean_str(other.get("created_at")), other_id_s))
        shared_terms = source_terms.intersection(_association_terms(other))
        if shared_terms:
            semantic_pool.append((len(shared_terms), _clean_str(other.get("created_at")), other_id_s))

    same_session.sort(reverse=True)
    for _created, cid in same_session[:10]:
        _add_candidate(candidates, cid, self_id=bead_id, beads=beads)
    semantic_pool.sort(reverse=True)
    for _score, _created, cid in semantic_pool[: max(1, int(max_candidates))]:
        _add_candidate(candidates, cid, self_id=bead_id, beads=beads)
    return candidates[: max(1, int(max_candidates))]


def _model_candidate_context(index: dict[str, Any], bead: dict[str, Any], candidate_ids: list[str]) -> dict[str, Any]:
    beads = index.get("beads") or {}
    return {
        "contract": "memory.association_candidate_scout.v1",
        "role": "cheap_candidate_scout",
        "source_bead": _bead_context(bead),
        "candidate_beads": {
            cid: _bead_context(beads.get(cid) or {})
            for cid in candidate_ids
            if cid in beads and isinstance(beads.get(cid), dict)
        },
        "allowed_relationships": sorted(INFERENCE_CANONICAL_RELATION_TYPES),
        "rules": [
            "Return possible association candidates only; do not assert graph truth.",
            "Every candidate must target one of candidate_beads.",
            "Use canonical relationships only.",
            "Prefer cross-domain business, evidence, temporal, causal, blocking, support, and contradiction signals.",
            "Omit weak lexical-only matches.",
        ],
        "response_shape": {
            "candidates": [
                {
                    "target_bead": "candidate bead id",
                    "relationship": "canonical relationship",
                    "reason_text": "why this deserves frontier judge review",
                    "reason_code": "short_snake_case",
                    "confidence": 0.0,
                    "evidence_refs": [],
                }
            ]
        },
    }


def _model_candidate_proposals_for_bead(
    index: dict[str, Any],
    bead: dict[str, Any],
    *,
    candidate_ids: list[str],
) -> list[dict[str, Any]]:
    bead_id = _clean_str(bead.get("id"))
    if not bead_id or not candidate_ids:
        return []
    context = _model_candidate_context(index, bead, candidate_ids)
    prompt = (
        "You are Core Memory's cheap association candidate scout. Your job is to "
        "raise plausible candidate links for a separate frontier association judge. "
        "You never write graph edges and you never mark a candidate as true. "
        "Return JSON only.\n\n"
        f"{json.dumps(context, ensure_ascii=False, sort_keys=True)}"
    )
    try:
        raw = chat_complete(
            prompt,
            config=_chat_config_with_model(
                "CORE_MEMORY_ASSOCIATION_CANDIDATE_MODEL",
                "CORE_MEMORY_CHEAP_MODEL",
            ),
            max_tokens=1200,
            temperature=0,
        )
        obj = _json_object(raw)
    except Exception:
        return []
    if not isinstance(obj, dict):
        return []

    allowed_targets = set(candidate_ids)
    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(_as_list(obj.get("candidates"))):
        if not isinstance(row, dict):
            continue
        target_id = _clean_str(row.get("target_bead") or row.get("target") or row.get("target_bead_id"))
        if target_id not in allowed_targets or target_id == bead_id:
            continue
        relationship = normalize_relation_type(_clean_str(row.get("relationship") or row.get("proposed_relationship")).lower())
        if relationship not in INFERENCE_CANONICAL_RELATION_TYPES:
            continue
        reason_text = _clean_str(row.get("reason_text") or row.get("rationale") or row.get("reason"))
        if not reason_text:
            continue
        confidence = row.get("confidence")
        try:
            confidence_f = min(0.95, max(0.05, float(confidence)))
        except Exception:
            confidence_f = 0.45
        reason_code = _clean_str(row.get("reason_code")) or f"model_candidate_{idx + 1}"
        rows.append(
            _candidate(
                bead_id,
                target_id,
                relationship,
                reason_text=reason_text,
                reason_code=reason_code,
                confidence=confidence_f,
                candidate_class="model_candidate_hint",
                evidence_refs=[x for x in _as_list(row.get("evidence_refs")) if x],
            )
        )
    return _dedupe_candidate_rows(rows)


def _candidate_proposals_for_bead(
    index: dict[str, Any],
    bead: dict[str, Any],
    *,
    explicit_candidate_ids: list[str],
    max_candidates: int,
) -> list[dict[str, Any]]:
    beads = index.get("beads") or {}
    bead_id = _clean_str(bead.get("id"))
    candidates = _candidate_ids_for_bead(
        index,
        bead,
        explicit_candidate_ids=explicit_candidate_ids,
        max_candidates=max_candidates,
    )
    generation_mode = _candidate_generation_mode()
    if generation_mode in {"llm", "model"}:
        return _model_candidate_proposals_for_bead(index, bead, candidate_ids=candidates)
    candidates_out: list[dict[str, Any]] = []

    prev_id = _clean_str(bead.get("prev_bead_id"))
    if prev_id in beads:
        candidates_out.append(
            _candidate(
                bead_id,
                prev_id,
                "follows",
                reason_text="Source bead follows the previous bead in the same session.",
                reason_code="session_temporal_adjacency",
                confidence=0.98,
                evidence_refs=[
                    {"bead_id": bead_id, "field": "prev_bead_id"},
                    {"bead_id": prev_id, "field": "id"},
                ],
            )
        )

    for target in _as_list(bead.get("supersedes")):
        target_id = _clean_str(target)
        if target_id in beads:
            candidates_out.append(
                _candidate(
                    bead_id,
                    target_id,
                    "supersedes",
                    reason_text="Source bead explicitly supersedes the target bead.",
                    reason_code="explicit_supersedes_field",
                    confidence=0.98,
                    evidence_refs=[
                        {"bead_id": bead_id, "field": "supersedes"},
                        {"bead_id": target_id, "field": "id"},
                    ],
                )
            )

    derived_ids = []
    for target in _as_list(bead.get("derived_from_bead_ids")):
        if _clean_str(target) in beads:
            derived_ids.append(_clean_str(target))
    derived_ids.extend(_resolve_reference_ids(index, _as_list(bead.get("derived_from"))))
    for target_id in dict.fromkeys(derived_ids):
        if target_id and target_id != bead_id:
            candidates_out.append(
                _candidate(
                    bead_id,
                    target_id,
                    "derived_from",
                    reason_text="Source bead declares the target bead as direct evidence.",
                    reason_code="explicit_derived_from",
                    confidence=0.95,
                    evidence_refs=[
                        {"bead_id": bead_id, "field": "derived_from"},
                        {"bead_id": target_id, "field": "id"},
                    ],
                )
            )

    if _clean_str(bead.get("type")) == "document_reference" and _has_section_scope(bead):
        for target_id in candidates:
            other = beads.get(target_id)
            if not isinstance(other, dict) or _clean_str(other.get("type")) != "document_reference":
                continue
            if _has_section_scope(other):
                continue
            if _doc_ids(bead).intersection(_doc_ids(other)) and _same_source(bead, other):
                candidates_out.append(
                    _candidate(
                        bead_id,
                        target_id,
                        "part_of",
                        reason_text="Section-scoped document bead belongs to the whole-document bead.",
                        reason_code="document_section_part_of_document",
                        confidence=0.98,
                        evidence_refs=[
                            {"bead_id": bead_id, "field": "document_id"},
                            {"bead_id": target_id, "field": "document_id"},
                        ],
                    )
                )
                break

    unifying_id = _clean_str(bead.get("core_memory_unifying_id"))
    if unifying_id:
        for target_id in candidates:
            other = beads.get(target_id)
            if not isinstance(other, dict):
                continue
            if _clean_str(other.get("core_memory_unifying_id")) != unifying_id:
                continue
            if target_id == bead_id:
                continue
            candidates_out.append(
                _candidate(
                    bead_id,
                    target_id,
                    "associated_with",
                    reason_text="Both beads share a stable cross-source unifying id.",
                    reason_code="shared_core_memory_unifying_id",
                    confidence=0.9,
                    evidence_refs=[
                        {"bead_id": bead_id, "field": "core_memory_unifying_id"},
                        {"bead_id": target_id, "field": "core_memory_unifying_id"},
                    ],
                )
            )

    source_record_id = _clean_str(bead.get("source_record_id") or bead.get("business_object_id"))
    if source_record_id:
        for target_id in candidates:
            other = beads.get(target_id)
            if not isinstance(other, dict):
                continue
            other_record = _clean_str(other.get("source_record_id") or other.get("business_object_id"))
            if other_record == source_record_id and _same_source(bead, other):
                candidates_out.append(
                    _candidate(
                        bead_id,
                        target_id,
                        "associated_with",
                        reason_text="Both beads refer to the same source object.",
                        reason_code="same_source_object",
                        confidence=0.88,
                        evidence_refs=[
                            {"bead_id": bead_id, "field": "source_record_id"},
                            {"bead_id": target_id, "field": "source_record_id"},
                        ],
                    )
                )

    transcript_id = _clean_str(bead.get("transcript_id") or bead.get("conversation_id"))
    if transcript_id and _clean_str(bead.get("type")) == "transcript":
        matches: list[tuple[str, str]] = []
        for target_id in candidates:
            other = beads.get(target_id)
            if not isinstance(other, dict):
                continue
            other_transcript = _clean_str(other.get("transcript_id") or other.get("conversation_id"))
            if other_transcript == transcript_id and _same_source(bead, other):
                matches.append((_clean_str(other.get("created_at")), target_id))
        matches.sort(reverse=True)
        if matches:
            candidates_out.append(
                _candidate(
                    bead_id,
                    matches[0][1],
                    "follows",
                    reason_text="Periodic transcript bead follows the prior snapshot for the same transcript.",
                    reason_code="periodic_transcript_snapshot_continuity",
                    confidence=0.9,
                    evidence_refs=[
                        {"bead_id": bead_id, "field": "transcript_id"},
                        {"bead_id": matches[0][1], "field": "transcript_id"},
                    ],
                )
            )

    if generation_mode == "hybrid":
        candidates_out.extend(
            _model_candidate_proposals_for_bead(index, bead, candidate_ids=candidates)
        )

    return _dedupe_candidate_rows(candidates_out)


def _dedupe_candidate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = (
            _clean_str(row.get("source_bead")),
            _clean_str(row.get("target_bead")),
            _clean_str(row.get("proposed_relationship")).lower(),
        )
        if not all(key) or key in seen or key[0] == key[1]:
            continue
        seen.add(key)
        out.append(row)
    return out


def _association_exists(index: dict[str, Any], source: str, target: str, relationship: str) -> bool:
    key = assoc_dedupe_key({
        "source_bead_id": source,
        "target_bead_id": target,
        "relationship": relationship,
    })
    for assoc in index.get("associations") or []:
        if not isinstance(assoc, dict):
            continue
        other = assoc_dedupe_key({
            "source_bead_id": assoc.get("source_bead") or assoc.get("source_bead_id"),
            "target_bead_id": assoc.get("target_bead") or assoc.get("target_bead_id"),
            "relationship": assoc.get("relationship"),
        })
        if other == key:
            return True
    return False


def _write_association_if_missing(
    root: str | Path,
    *,
    source: str,
    target: str,
    relationship: str,
    confidence: float,
    reason_text: str,
    edge_class: str,
    provenance: str,
    reason_code: str = "",
    evidence_bead_ids: list[str] | None = None,
    evidence_refs: list[Any] | None = None,
    judge_model: str = "",
    prompt_version: str = "",
    rubric_version: str = "",
    grounding_hash: str = "",
    truth_basis: str = "",
    candidate_ids: list[str] | None = None,
    association_run_id: str = "",
    association_policy_version: str = "",
) -> dict[str, Any]:
    root_path = Path(root)
    source_id = _clean_str(source)
    target_id = _clean_str(target)
    rel = _clean_str(relationship).lower()
    if not source_id or not target_id or not rel or source_id == target_id:
        return {"ok": False, "error": "invalid_association_edge"}

    assoc: dict[str, Any] | None = None
    with store_lock(root_path):
        index_path = root_path / ".beads" / "index.json"
        index = _load_index(root_path)
        beads = index.get("beads") or {}
        if source_id not in beads or target_id not in beads:
            return {"ok": False, "error": "bead_not_found"}
        if _association_exists(index, source_id, target_id, rel):
            return {"ok": True, "deduped": True, "association_id": ""}

        assoc = {
            "id": f"assoc-{uuid.uuid4().hex[:12].upper()}",
            "type": "association",
            "source_bead": source_id,
            "target_bead": target_id,
            "relationship": rel,
            "status": "active",
            "edge_class": _clean_str(edge_class) or "system_structural",
            "confidence": float(confidence),
            "reason_text": _clean_str(reason_text),
            "rationale": _clean_str(reason_text),
            "provenance": _clean_str(provenance) or "system_structural",
            "reason_code": _clean_str(reason_code) or None,
            "evidence_bead_ids": list(evidence_bead_ids or []),
            "evidence_refs": list(evidence_refs or []),
            "judge_model": _clean_str(judge_model) or None,
            "prompt_version": _clean_str(prompt_version) or None,
            "rubric_version": _clean_str(rubric_version) or None,
            "grounding_hash": _clean_str(grounding_hash) or None,
            "truth_basis": _clean_str(truth_basis) or None,
            "candidate_ids": list(candidate_ids or []),
            "association_run_id": _clean_str(association_run_id) or None,
            "association_policy_version": _clean_str(association_policy_version) or None,
            "created_at": _now(),
        }
        assoc = {k: v for k, v in assoc.items() if v not in (None, [], {})}
        index.setdefault("associations", []).append(assoc)
        index.setdefault("stats", {})["total_associations"] = len(index.get("associations") or [])
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        events.event_association_created(root_path, assoc, use_lock=False)
        mark_trace_dirty(root_path, reason="association_coverage")

    if assoc is not None:
        _mirror_association_to_graph(root_path, assoc)
    return {"ok": True, "deduped": False, "association_id": _clean_str((assoc or {}).get("id"))}


class AssociationJudgeUnavailable(RuntimeError):
    pass


class LLMAssociationJudge:
    def review(self, context: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            "You are Core Memory's association judge. Review candidate bead associations "
            "against the supplied bead content, metadata, provenance, and evidence refs. "
            "Return only JSON matching contract memory.association_judge.v1. "
            "Allowed decision actions: accept, reject, modify, invert, replace, add, no_link. "
            "Do not approve unsupported links. linked/no_supported_links require your decision.\n\n"
            f"{json.dumps(context, ensure_ascii=False, sort_keys=True)}"
        )
        raw = chat_complete(
            prompt,
            config=_chat_config_with_model(
                "CORE_MEMORY_ASSOCIATION_JUDGE_MODEL",
                "CORE_MEMORY_FRONTIER_MODEL",
            ),
            max_tokens=1800,
            temperature=0,
        )
        text = _clean_str(raw)
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        return json.loads(text)


def _configured_association_judge() -> Any | None:
    mode = _clean_str(os.environ.get("CORE_MEMORY_ASSOCIATION_JUDGE_MODE")).lower()
    if mode in {"llm", "model"}:
        return LLMAssociationJudge()
    return None


def _invoke_association_judge(context: dict[str, Any], judge: Any | None = None) -> dict[str, Any]:
    active = judge if judge is not None else _configured_association_judge()
    if active is None:
        raise AssociationJudgeUnavailable("association_judge_not_configured")
    if hasattr(active, "review"):
        out = active.review(context)
    elif callable(active):
        out = active(context)
    else:
        raise AssociationJudgeUnavailable("association_judge_invalid")
    if not isinstance(out, dict):
        raise ValueError("association_judge_returned_non_object")
    return out


def _bead_context(bead: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id", "type", "title", "summary", "detail", "session_id", "source_turn_ids",
        "tags", "entities", "entity_refs", "topics", "created_at", "prev_bead_id", "next_bead_id",
        "source_id", "source_event_id", "source_system", "source_kind",
        "core_memory_unifying_id", "document_id", "ragie_document_id",
        "raw_source_object_id", "section_refs", "source_record_id",
        "source_table", "business_object_type", "business_object_id",
        "record_action", "record_grain", "metric_name", "metric_value",
        "metric_unit", "change_pct", "currency", "attribute_tags",
        "as_of_timestamp", "observed_at", "effective_from", "effective_to",
        "state_change", "claims", "supporting_facts", "because", "actor",
        "assertion_kind", "assertion_subject", "assertion_predicate", "assertion_value",
        "transcript_id", "conversation_id", "source_thread_id", "message_refs",
        "speaker_refs", "supersedes", "derived_from", "derived_from_bead_ids", "evidence_refs",
    ]
    out: dict[str, Any] = {}
    for key in keys:
        value = bead.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "detail":
            out[key] = _clean_str(value)[:1200]
        else:
            out[key] = value
    return out


def _build_judge_context(
    *,
    index: dict[str, Any],
    run_id: str,
    trigger: str,
    source_bead_ids: list[str],
    candidates: list[dict[str, Any]],
    policy_version: str,
    prompt_version: str,
    rubric_version: str,
) -> dict[str, Any]:
    beads = index.get("beads") or {}
    context_ids = set(source_bead_ids)
    for candidate in candidates:
        context_ids.add(_clean_str(candidate.get("source_bead")))
        context_ids.add(_clean_str(candidate.get("target_bead")))
        for bid in candidate.get("evidence_bead_ids") or []:
            context_ids.add(_clean_str(bid))
    return {
        "contract": ASSOCIATION_JUDGE_CONTRACT,
        "run_id": run_id,
        "trigger": trigger,
        "policy_version": policy_version,
        "prompt_version": prompt_version,
        "rubric_version": rubric_version,
        "source_bead_ids": source_bead_ids,
        "candidates": candidates,
        "beads": {
            bid: _bead_context(beads.get(bid) or {})
            for bid in sorted(x for x in context_ids if x and isinstance(beads.get(x), dict))
        },
        "rules": [
            "Candidate proposals are not graph truth.",
            "Approve only evidence-grounded associations.",
            "Use canonical relationships and explicit reason_text.",
            "You may accept, reject, modify, invert, replace, add, or no_link.",
            "Return reviewed_beads with linked or no_supported_links where applicable.",
        ],
    }


def _grounding_hash_for_judge(context: dict[str, Any], result: dict[str, Any]) -> str:
    explicit = _clean_str(result.get("grounding_hash"))
    if explicit:
        return explicit
    basis = {
        "run_id": _clean_str(context.get("run_id")),
        "candidates": [c.get("candidate_id") for c in context.get("candidates") or []],
        "source_bead_ids": list(context.get("source_bead_ids") or []),
        "judge_model": _clean_str(result.get("judge_model")) or "configured-association-judge",
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _decision_state(value: Any) -> str:
    state = _clean_str(value).lower()
    if state in {"no_link", "none"}:
        return "no_supported_links"
    if state in {"linked", "no_supported_links", "quarantined", "judge_failed", "failed", "pending_judge", "skipped_ineligible"}:
        return state
    return ""


def _candidate_association_payload(
    decision: dict[str, Any],
    candidate_by_id: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    action = _clean_str(decision.get("action")).lower()
    candidate_id = _clean_str(decision.get("candidate_id"))
    candidate = candidate_by_id.get(candidate_id) if candidate_id else None
    candidate_ids = [candidate_id] if candidate_id else []

    if action in {"reject", "no_link"}:
        return None, candidate_ids

    if action == "accept" and candidate:
        source = _clean_str(candidate.get("source_bead"))
        target = _clean_str(candidate.get("target_bead"))
        rel = _clean_str(candidate.get("proposed_relationship"))
        confidence = decision.get("confidence", candidate.get("confidence_prior"))
        reason_text = _clean_str(decision.get("reason_text")) or _clean_str(candidate.get("system_rationale"))
        evidence_refs = list(decision.get("evidence_refs") or candidate.get("evidence_refs") or [])
        evidence_bead_ids = list(decision.get("evidence_bead_ids") or candidate.get("evidence_bead_ids") or [])
    elif action == "invert" and candidate:
        source = _clean_str(decision.get("source_bead") or candidate.get("target_bead"))
        target = _clean_str(decision.get("target_bead") or candidate.get("source_bead"))
        rel = _clean_str(decision.get("relationship") or candidate.get("proposed_relationship"))
        confidence = decision.get("confidence", candidate.get("confidence_prior"))
        reason_text = _clean_str(decision.get("reason_text")) or _clean_str(candidate.get("system_rationale"))
        evidence_refs = list(decision.get("evidence_refs") or candidate.get("evidence_refs") or [])
        evidence_bead_ids = list(decision.get("evidence_bead_ids") or candidate.get("evidence_bead_ids") or [])
    else:
        source = _clean_str(decision.get("source_bead") or (candidate or {}).get("source_bead"))
        target = _clean_str(decision.get("target_bead") or (candidate or {}).get("target_bead"))
        rel = _clean_str(decision.get("relationship") or (candidate or {}).get("proposed_relationship"))
        confidence = decision.get("confidence", (candidate or {}).get("confidence_prior"))
        reason_text = _clean_str(decision.get("reason_text")) or _clean_str((candidate or {}).get("system_rationale"))
        evidence_refs = list(decision.get("evidence_refs") or (candidate or {}).get("evidence_refs") or [])
        evidence_bead_ids = list(decision.get("evidence_bead_ids") or (candidate or {}).get("evidence_bead_ids") or [])

    return {
        "source_bead": source,
        "target_bead": target,
        "relationship": rel,
        "confidence": confidence,
        "reason_text": reason_text,
        "provenance": "model_inferred",
        "truth_basis": _clean_str(decision.get("truth_basis")),
        "reason_code": _clean_str(decision.get("reason_code") or (candidate or {}).get("reason_code")),
        "evidence_bead_ids": evidence_bead_ids,
        "evidence_refs": evidence_refs,
    }, candidate_ids


def _apply_judge_result(
    root: str | Path,
    *,
    index: dict[str, Any],
    run_id: str,
    session_id: str | None,
    source_bead_ids: list[str],
    candidates: list[dict[str, Any]],
    judge_context: dict[str, Any],
    judge_result: dict[str, Any],
    policy_version: str,
    prompt_version: str,
    rubric_version: str,
) -> dict[str, Any]:
    beads = index.get("beads") or {}
    candidate_by_id = {_clean_str(c.get("candidate_id")): c for c in candidates if _clean_str(c.get("candidate_id"))}
    judge_model = _clean_str(judge_result.get("judge_model")) or "configured-association-judge"
    prompt_v = _clean_str(judge_result.get("prompt_version")) or prompt_version
    rubric_v = _clean_str(judge_result.get("rubric_version")) or rubric_version
    grounding_hash = _grounding_hash_for_judge(judge_context, judge_result)

    accepted = 0
    rejected = 0
    appended = 0
    deduped = 0
    quarantined = 0
    failed = 0
    association_ids: list[str] = []
    errors: list[dict[str, Any]] = []
    state_by_bead: dict[str, str] = {bid: "pending_judge" for bid in source_bead_ids}

    def mark(bead_id: str, state: str) -> None:
        bid = _clean_str(bead_id)
        normalized = _decision_state(state)
        if not bid or not normalized:
            return
        priority = {
            "skipped_ineligible": 0,
            "pending_judge": 1,
            "no_supported_links": 2,
            "quarantined": 3,
            "failed": 4,
            "judge_failed": 4,
            "linked": 5,
        }
        current = state_by_bead.get(bid)
        if current is None or priority.get(normalized, 0) >= priority.get(current, 0):
            state_by_bead[bid] = normalized

    reviewed = [x for x in (judge_result.get("reviewed_beads") or []) if isinstance(x, dict)]
    for row in reviewed:
        reviewed_state = _decision_state(row.get("association_state"))
        if reviewed_state == "linked":
            continue
        mark(_clean_str(row.get("bead_id")), reviewed_state)

    decisions = [x for x in (judge_result.get("decisions") or []) if isinstance(x, dict)]
    new_associations = [x for x in (judge_result.get("new_associations") or []) if isinstance(x, dict)]
    for row in new_associations:
        item = dict(row)
        item.setdefault("action", "add")
        decisions.append(item)

    for decision in decisions:
        action = _clean_str(decision.get("action")).lower()
        if action not in JUDGE_ACTIONS:
            quarantined += 1
            write_quarantine(
                Path(root),
                decision,
                reasons=[f"invalid_judge_action:{action or 'empty'}"],
                warnings=[],
                original_payload=decision,
                session_id=_clean_str(session_id),
            )
            continue
        if action in {"reject", "no_link"}:
            rejected += 1
            candidate = candidate_by_id.get(_clean_str(decision.get("candidate_id"))) or {}
            mark(_clean_str(decision.get("source_bead") or candidate.get("source_bead")), "no_supported_links")
            continue

        assoc_payload, candidate_ids = _candidate_association_payload(decision, candidate_by_id)
        assoc_payload = dict(assoc_payload or {})
        assoc_payload["judge_model"] = judge_model
        assoc_payload["prompt_version"] = prompt_v
        assoc_payload["rubric_version"] = rubric_v
        assoc_payload["grounding_hash"] = grounding_hash

        quarantine_reasons: list[str] = []
        if not _clean_str(assoc_payload.get("truth_basis")):
            quarantine_reasons.append("missing_truth_basis")
        validated = validate_and_normalize_inference_payload(assoc_payload, mode=INFERENCE_MODE_STRICT)
        if not validated.ok:
            quarantine_reasons.extend(list(validated.quarantine_reasons))
        row = validated.record
        src = _clean_str(row.get("source_bead"))
        tgt = _clean_str(row.get("target_bead"))
        if src not in beads or tgt not in beads:
            quarantine_reasons.append("bead_not_found")

        if quarantine_reasons:
            quarantined += 1
            mark(src, "quarantined")
            mark(tgt, "quarantined")
            write_quarantine(
                Path(root),
                row,
                reasons=quarantine_reasons,
                warnings=list(validated.warnings),
                original_payload=decision,
                session_id=_clean_str(session_id),
            )
            continue

        accepted += 1
        out = _write_association_if_missing(
            root,
            source=src,
            target=tgt,
            relationship=_clean_str(row.get("relationship")),
            confidence=float(row.get("confidence") or 0.0),
            reason_text=_clean_str(row.get("reason_text")),
            edge_class="agent_judged",
            provenance=_clean_str(row.get("provenance")) or "model_inferred",
            reason_code=_clean_str(row.get("reason_code")),
            evidence_bead_ids=list(row.get("evidence_bead_ids") or []),
            evidence_refs=list(row.get("evidence_refs") or []),
            judge_model=judge_model,
            prompt_version=prompt_v,
            rubric_version=rubric_v,
            grounding_hash=grounding_hash,
            truth_basis=_clean_str(assoc_payload.get("truth_basis")),
            candidate_ids=candidate_ids,
            association_run_id=run_id,
            association_policy_version=policy_version,
        )
        if out.get("ok") and out.get("deduped"):
            deduped += 1
            mark(src, "linked")
            mark(tgt, "linked")
        elif out.get("ok"):
            appended += 1
            mark(src, "linked")
            mark(tgt, "linked")
            if _clean_str(out.get("association_id")):
                association_ids.append(_clean_str(out.get("association_id")))
        else:
            failed += 1
            mark(src, "failed")
            errors.append({"decision": decision, "error": out.get("error")})

    _append_judge_decision_record(
        root,
        {
            "contract": ASSOCIATION_JUDGE_CONTRACT,
            "run_id": run_id,
            "session_id": _clean_str(session_id),
            "judge_model": judge_model,
            "prompt_version": prompt_v,
            "rubric_version": rubric_v,
            "grounding_hash": grounding_hash,
            "candidate_ids": [_clean_str(c.get("candidate_id")) for c in candidates],
            "decisions": decisions,
            "reviewed_beads": reviewed,
            "association_ids": association_ids,
            "counts": {
                "accepted": accepted,
                "rejected": rejected,
                "appended": appended,
                "deduped": deduped,
                "quarantined": quarantined,
                "failed": failed,
            },
            "errors": errors,
        },
    )

    return {
        "accepted": accepted,
        "rejected": rejected,
        "appended": appended,
        "deduped": deduped,
        "quarantined": quarantined,
        "failed": failed,
        "association_ids": association_ids,
        "association_state_by_bead": state_by_bead,
        "judge_model": judge_model,
        "prompt_version": prompt_v,
        "rubric_version": rubric_v,
        "grounding_hash": grounding_hash,
        "errors": errors,
    }


def enqueue_association_coverage(
    root: str | Path,
    *,
    bead_ids: list[str] | None = None,
    session_id: str | None = None,
    trigger: str = "operator",
    candidate_bead_ids: list[str] | None = None,
    run_inline: bool = False,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    policy_version: str = POLICY_VERSION,
    prompt_version: str = JUDGE_PROMPT_VERSION,
    rubric_version: str = JUDGE_RUBRIC_VERSION,
    graph_revision: str | None = None,
    judge: Any | None = None,
) -> dict[str, Any]:
    index = _load_index(root)
    resolved_ids = [_clean_str(x) for x in (bead_ids or []) if _clean_str(x)]
    if not resolved_ids and _clean_str(session_id):
        resolved_ids = _resolve_session_bead_ids(index, session_id)
    resolved_ids = [bid for bid in dict.fromkeys(resolved_ids) if bid in (index.get("beads") or {})]
    skipped_ids = [bid for bid in resolved_ids if not _coverage_eligible((index.get("beads") or {}).get(bid))]
    eligible_ids = [bid for bid in resolved_ids if bid not in set(skipped_ids)]
    if not resolved_ids:
        return {
            "ok": False,
            "error": "association_run_requires_bead_ids_or_session_id",
            "contract": "memory.association_run.v1",
        }

    run_id = f"arun-{uuid.uuid4().hex[:12]}"
    trigger_n = _normalize_trigger(trigger)
    initial_states = {bid: "deferred" for bid in eligible_ids}
    initial_states.update({bid: "skipped_ineligible" for bid in skipped_ids})
    graph_rev = _clean_str(graph_revision) or _graph_revision(index)
    prompt_v = _clean_str(prompt_version) or JUDGE_PROMPT_VERSION
    rubric_v = _clean_str(rubric_version) or JUDGE_RUBRIC_VERSION
    candidate_sig = _bead_set_signature([_clean_str(x) for x in (candidate_bead_ids or []) if _clean_str(x)]) if candidate_bead_ids else "auto"

    if not eligible_ids:
        out = {
            "ok": True,
            "contract": "memory.association_run.v1",
            "run_id": run_id,
            "status": "completed",
            "trigger": trigger_n,
            "policy_version": _clean_str(policy_version) or POLICY_VERSION,
            "prompt_version": prompt_v,
            "rubric_version": rubric_v,
            "graph_revision": graph_rev,
            "session_id": _clean_str(session_id),
            "bead_ids": [],
            "skipped_bead_ids": skipped_ids,
            "association_state_by_bead": initial_states,
            "counts": {"appended": 0, "deduped": 0, "quarantined": 0, "failed": 0, "skipped": len(skipped_ids)},
        }
        _append_run_record(root, out)
        return out

    if run_inline:
        return run_association_coverage(
            root=root,
            run_id=run_id,
            bead_ids=eligible_ids,
            session_id=session_id,
            trigger=trigger_n,
            candidate_bead_ids=list(candidate_bead_ids or []),
            max_candidates=max_candidates,
            policy_version=policy_version,
            prompt_version=prompt_v,
            rubric_version=rubric_v,
            graph_revision=graph_rev,
            judge=judge,
            skipped_bead_ids=skipped_ids,
        )

    bead_set_sig = _bead_set_signature(eligible_ids)
    idempotency_key = (
        f"assoc:{_clean_str(policy_version) or POLICY_VERSION}:"
        f"trigger:{trigger_n}:"
        f"session:{_clean_str(session_id) or '-'}:"
        f"beads:{bead_set_sig}:"
        f"candidates:{candidate_sig}:"
        f"max:{max(1, int(max_candidates))}:"
        f"graph:{graph_rev}:"
        f"prompt:{prompt_v}:"
        f"rubric:{rubric_v}:"
        f"cgen:{CANDIDATE_GENERATION_VERSION}"
    )
    from core_memory.runtime.queue.side_effect_queue import enqueue_side_effect_event

    queue = enqueue_side_effect_event(
        root=root,
        kind="association-pass",
        payload={
            "run_id": run_id,
            "bead_ids": eligible_ids,
            "skipped_bead_ids": skipped_ids,
            "session_id": _clean_str(session_id),
            "trigger": trigger_n,
            "candidate_bead_ids": list(candidate_bead_ids or []),
            "max_candidates": max(1, int(max_candidates)),
            "policy_version": _clean_str(policy_version) or POLICY_VERSION,
            "prompt_version": prompt_v,
            "rubric_version": rubric_v,
            "graph_revision": graph_rev,
            "candidate_generation_version": CANDIDATE_GENERATION_VERSION,
        },
        idempotency_key=idempotency_key,
    )
    _append_run_record(
        root,
        {
            "run_id": run_id,
            "status": "queued" if queue.get("ok") else "failed",
            "trigger": trigger_n,
            "policy_version": _clean_str(policy_version) or POLICY_VERSION,
            "prompt_version": prompt_v,
            "rubric_version": rubric_v,
            "graph_revision": graph_rev,
            "session_id": _clean_str(session_id),
            "bead_ids": eligible_ids,
            "skipped_bead_ids": skipped_ids,
            "association_state_by_bead": initial_states,
            "queued_job_id": _clean_str(queue.get("id")),
            "queue": queue,
            "counts": {"appended": 0, "deduped": 0, "quarantined": 0, "failed": 0, "skipped": len(skipped_ids)},
            "contract": "memory.association_run.v1",
        },
    )
    return {
        "ok": bool(queue.get("ok")),
        "contract": "memory.association_run.v1",
        "run_id": run_id,
        "status": "queued" if queue.get("ok") else "failed",
        "bead_ids": eligible_ids,
        "skipped_bead_ids": skipped_ids,
        "association_state_by_bead": initial_states,
        "queued_job_id": _clean_str(queue.get("id")),
        "association_queued": bool(queue.get("ok")),
        "queue": queue,
    }


def on_bead_committed(
    root: str | Path,
    bead_id: str,
    *,
    trigger: str = "bead_committed",
    source: str = "memory_store",
    run_inline: bool = False,
    session_id: str | None = None,
    graph_revision: str | None = None,
    policy_version: str | None = None,
    judge: Any | None = None,
    enqueue: bool = True,
) -> dict[str, Any] | None:
    bid = _clean_str(bead_id)
    if not bid:
        return None
    if not enqueue and not run_inline:
        index = _load_index(root)
        beads = index.get("beads") or {}
        if bid not in beads:
            return None
        state = "deferred" if _coverage_eligible(beads.get(bid)) else "skipped_ineligible"
        run_id = f"arun-{uuid.uuid4().hex[:12]}"
        out = {
            "ok": True,
            "contract": "memory.association_run.v1",
            "run_id": run_id,
            "status": "deferred" if state == "deferred" else "completed",
            "trigger": _normalize_trigger(trigger),
            "policy_version": _clean_str(policy_version) or POLICY_VERSION,
            "prompt_version": JUDGE_PROMPT_VERSION,
            "rubric_version": JUDGE_RUBRIC_VERSION,
            "graph_revision": _clean_str(graph_revision) or _graph_revision(index),
            "session_id": _clean_str(session_id),
            "bead_ids": [bid] if state == "deferred" else [],
            "skipped_bead_ids": [bid] if state == "skipped_ineligible" else [],
            "association_state_by_bead": {bid: state},
            "association_queued": False,
            "bead_commit_source": _clean_str(source) or "memory_store",
            "counts": {"appended": 0, "deduped": 0, "quarantined": 0, "failed": 0, "skipped": 1 if state == "skipped_ineligible" else 0},
        }
        _append_run_record(root, out)
        return out
    out = enqueue_association_coverage(
        root=root,
        bead_ids=[bid],
        session_id=session_id,
        trigger=trigger,
        run_inline=run_inline,
        policy_version=_clean_str(policy_version) or POLICY_VERSION,
        graph_revision=graph_revision,
        judge=judge,
    )
    if isinstance(out, dict):
        out["bead_commit_source"] = _clean_str(source) or "memory_store"
    return out


def run_association_coverage(
    root: str | Path,
    *,
    run_id: str | None = None,
    bead_ids: list[str] | None = None,
    session_id: str | None = None,
    trigger: str = "operator",
    candidate_bead_ids: list[str] | None = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    policy_version: str = POLICY_VERSION,
    prompt_version: str = JUDGE_PROMPT_VERSION,
    rubric_version: str = JUDGE_RUBRIC_VERSION,
    graph_revision: str | None = None,
    judge: Any | None = None,
    skipped_bead_ids: list[str] | None = None,
) -> dict[str, Any]:
    run_id_final = _clean_str(run_id) or f"arun-{uuid.uuid4().hex[:12]}"
    trigger_n = _normalize_trigger(trigger)
    index = _load_index(root)
    beads = index.get("beads") or {}
    resolved_ids = [_clean_str(x) for x in (bead_ids or []) if _clean_str(x)]
    if not resolved_ids and _clean_str(session_id):
        resolved_ids = _resolve_session_bead_ids(index, session_id)
    resolved_ids = [bid for bid in dict.fromkeys(resolved_ids) if bid in beads]
    skipped_ids = [_clean_str(x) for x in (skipped_bead_ids or []) if _clean_str(x)]
    skipped_ids.extend([bid for bid in resolved_ids if not _coverage_eligible(beads.get(bid))])
    skipped_ids = list(dict.fromkeys([bid for bid in skipped_ids if bid in beads]))
    eligible_ids = [bid for bid in resolved_ids if bid not in set(skipped_ids) and _coverage_eligible(beads.get(bid))]
    prompt_v = _clean_str(prompt_version) or JUDGE_PROMPT_VERSION
    rubric_v = _clean_str(rubric_version) or JUDGE_RUBRIC_VERSION
    graph_rev = _clean_str(graph_revision) or _graph_revision(index)

    if not eligible_ids and not skipped_ids:
        out = {
            "ok": False,
            "contract": "memory.association_run.v1",
            "run_id": run_id_final,
            "status": "failed",
            "error": "association_run_requires_bead_ids_or_session_id",
            "bead_ids": [],
            "association_state_by_bead": {},
            "counts": {"appended": 0, "deduped": 0, "quarantined": 0, "failed": 0},
        }
        _append_run_record(root, out)
        return out

    if not eligible_ids:
        state_by_bead = {bid: "skipped_ineligible" for bid in skipped_ids}
        out = {
            "ok": True,
            "contract": "memory.association_run.v1",
            "run_id": run_id_final,
            "status": "completed",
            "trigger": trigger_n,
            "policy_version": _clean_str(policy_version) or POLICY_VERSION,
            "prompt_version": prompt_v,
            "rubric_version": rubric_v,
            "graph_revision": graph_rev,
            "session_id": _clean_str(session_id),
            "bead_ids": [],
            "skipped_bead_ids": skipped_ids,
            "association_state_by_bead": state_by_bead,
            "counts": {"appended": 0, "deduped": 0, "quarantined": 0, "failed": 0, "skipped": len(skipped_ids)},
        }
        _append_run_record(root, out)
        return out

    pending_states = {bid: "pending_judge" for bid in eligible_ids}
    pending_states.update({bid: "skipped_ineligible" for bid in skipped_ids})
    _append_run_record(
        root,
        {
            "run_id": run_id_final,
            "status": "running",
            "trigger": trigger_n,
            "policy_version": _clean_str(policy_version) or POLICY_VERSION,
            "prompt_version": prompt_v,
            "rubric_version": rubric_v,
            "graph_revision": graph_rev,
            "session_id": _clean_str(session_id),
            "bead_ids": eligible_ids,
            "skipped_bead_ids": skipped_ids,
            "association_state_by_bead": pending_states,
            "counts": {"appended": 0, "deduped": 0, "quarantined": 0, "failed": 0, "skipped": len(skipped_ids)},
            "contract": "memory.association_run.v1",
        },
    )

    explicit_candidates = [_clean_str(x) for x in (candidate_bead_ids or []) if _clean_str(x)]
    candidates: list[dict[str, Any]] = []
    candidate_state = {bid: "pending_judge" for bid in eligible_ids}
    candidate_state.update({bid: "skipped_ineligible" for bid in skipped_ids})
    candidate_generation_mode = _candidate_generation_mode()

    for bead_id in eligible_ids:
        bead = beads.get(bead_id)
        if not isinstance(bead, dict):
            candidate_state[bead_id] = "failed"
            continue
        candidates.extend(
            _candidate_proposals_for_bead(
                index,
                bead,
                explicit_candidate_ids=explicit_candidates,
                max_candidates=max_candidates,
            )
        )
    candidates = _dedupe_candidate_rows(candidates)
    _append_candidate_record(
        root,
        {
            "run_id": run_id_final,
            "trigger": trigger_n,
            "policy_version": _clean_str(policy_version) or POLICY_VERSION,
            "prompt_version": prompt_v,
            "rubric_version": rubric_v,
            "graph_revision": graph_rev,
            "candidate_generation_version": CANDIDATE_GENERATION_VERSION,
            "candidate_generation_mode": candidate_generation_mode,
            "source_bead_ids": eligible_ids,
            "explicit_candidate_bead_ids": explicit_candidates,
            "max_candidates": max(1, int(max_candidates)),
            "candidate_count": len(candidates),
            "candidates": candidates,
        },
    )

    judge_context = _build_judge_context(
        index=index,
        run_id=run_id_final,
        trigger=trigger_n,
        source_bead_ids=eligible_ids,
        candidates=candidates,
        policy_version=_clean_str(policy_version) or POLICY_VERSION,
        prompt_version=prompt_v,
        rubric_version=rubric_v,
    )
    try:
        judge_result = _invoke_association_judge(judge_context, judge=judge)
    except AssociationJudgeUnavailable as exc:
        state_by_bead = {bid: "pending_judge" for bid in eligible_ids}
        state_by_bead.update({bid: "skipped_ineligible" for bid in skipped_ids})
        out = {
            "ok": True,
            "contract": "memory.association_run.v1",
            "run_id": run_id_final,
            "status": "pending_judge",
            "trigger": trigger_n,
            "policy_version": _clean_str(policy_version) or POLICY_VERSION,
            "prompt_version": prompt_v,
            "rubric_version": rubric_v,
            "graph_revision": graph_rev,
            "session_id": _clean_str(session_id),
            "bead_ids": eligible_ids,
            "skipped_bead_ids": skipped_ids,
            "association_state_by_bead": state_by_bead,
            "candidate_count": len(candidates),
            "counts": {
                "appended": 0,
                "deduped": 0,
                "quarantined": 0,
                "failed": 0,
                "pending_judge": len(eligible_ids),
                "skipped": len(skipped_ids),
            },
            "warning": _clean_str(exc),
        }
        _append_judge_decision_record(
            root,
            {
                "contract": ASSOCIATION_JUDGE_CONTRACT,
                "run_id": run_id_final,
                "status": "pending_judge",
                "warning": _clean_str(exc),
                "candidate_ids": [_clean_str(c.get("candidate_id")) for c in candidates],
                "source_bead_ids": eligible_ids,
            },
        )
        _append_run_record(root, out)
        return out
    except Exception as exc:
        state_by_bead = {bid: "judge_failed" for bid in eligible_ids}
        state_by_bead.update({bid: "skipped_ineligible" for bid in skipped_ids})
        out = {
            "ok": False,
            "contract": "memory.association_run.v1",
            "run_id": run_id_final,
            "status": "judge_failed",
            "trigger": trigger_n,
            "policy_version": _clean_str(policy_version) or POLICY_VERSION,
            "prompt_version": prompt_v,
            "rubric_version": rubric_v,
            "graph_revision": graph_rev,
            "session_id": _clean_str(session_id),
            "bead_ids": eligible_ids,
            "skipped_bead_ids": skipped_ids,
            "association_state_by_bead": state_by_bead,
            "candidate_count": len(candidates),
            "counts": {"appended": 0, "deduped": 0, "quarantined": 0, "failed": len(eligible_ids), "skipped": len(skipped_ids)},
            "error": _clean_str(exc),
        }
        _append_judge_decision_record(
            root,
            {
                "contract": ASSOCIATION_JUDGE_CONTRACT,
                "run_id": run_id_final,
                "status": "judge_failed",
                "error": _clean_str(exc),
                "candidate_ids": [_clean_str(c.get("candidate_id")) for c in candidates],
                "source_bead_ids": eligible_ids,
            },
        )
        _append_run_record(root, out)
        return out

    applied = _apply_judge_result(
        root,
        index=index,
        run_id=run_id_final,
        session_id=session_id,
        source_bead_ids=eligible_ids,
        candidates=candidates,
        judge_context=judge_context,
        judge_result=judge_result,
        policy_version=_clean_str(policy_version) or POLICY_VERSION,
        prompt_version=prompt_v,
        rubric_version=rubric_v,
    )
    state_by_bead = dict(applied.get("association_state_by_bead") or {})
    for bid in eligible_ids:
        state_by_bead.setdefault(bid, "pending_judge")
    state_by_bead.update({bid: "skipped_ineligible" for bid in skipped_ids})
    failed = int(applied.get("failed") or 0)
    quarantined = int(applied.get("quarantined") or 0)
    unresolved_ids = [bid for bid in eligible_ids if state_by_bead.get(bid) == "pending_judge"]
    errors = list(applied.get("errors") or [])
    for bid in unresolved_ids:
        errors.append({"code": "unresolved_judge_decision", "bead_id": bid})
    effective_failed = failed + len(unresolved_ids)
    status = "quarantined" if quarantined else ("failed" if effective_failed else "completed")
    counts = {
        "accepted": int(applied.get("accepted") or 0),
        "rejected": int(applied.get("rejected") or 0),
        "appended": int(applied.get("appended") or 0),
        "deduped": int(applied.get("deduped") or 0),
        "quarantined": quarantined,
        "failed": effective_failed,
        "pending_judge": len(unresolved_ids),
        "skipped": len(skipped_ids),
        "candidates": len(candidates),
    }
    out = {
        "ok": effective_failed == 0 and quarantined == 0,
        "contract": "memory.association_run.v1",
        "run_id": run_id_final,
        "status": status,
        "trigger": trigger_n,
        "policy_version": _clean_str(policy_version) or POLICY_VERSION,
        "prompt_version": prompt_v,
        "rubric_version": rubric_v,
        "graph_revision": graph_rev,
        "session_id": _clean_str(session_id),
        "bead_ids": eligible_ids,
        "skipped_bead_ids": skipped_ids,
        "association_state_by_bead": state_by_bead,
        "association_ids": list(applied.get("association_ids") or []),
        "candidate_count": len(candidates),
        "judge_model": _clean_str(applied.get("judge_model")),
        "grounding_hash": _clean_str(applied.get("grounding_hash")),
        "counts": counts,
        "errors": errors,
    }
    _append_run_record(root, out)
    return out


def apply_association_proposals(
    root: str | Path,
    *,
    associations: list[dict[str, Any]],
    run_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    index = _load_index(root)
    beads = index.get("beads") or {}
    accepted = 0
    appended = 0
    deduped = 0
    quarantined = 0
    association_ids: list[str] = []
    errors: list[dict[str, Any]] = []
    state_by_bead: dict[str, str] = {}

    def proposal_source(payload: dict[str, Any] | None, row: dict[str, Any] | None = None) -> str:
        for candidate in (row or {}, payload or {}):
            src = _clean_str(candidate.get("source_bead") or candidate.get("source_bead_id"))
            if src:
                return src
        return ""

    def mark_state(source_bead: str, state: str) -> None:
        src = _clean_str(source_bead)
        if not src:
            return
        priority = {"quarantined": 1, "failed": 2, "linked": 3}
        current = state_by_bead.get(src)
        if current is None or priority.get(state, 0) >= priority.get(current, 0):
            state_by_bead[src] = state

    for raw in list(associations or []):
        if not isinstance(raw, dict):
            quarantined += 1
            continue
        validated = validate_and_normalize_inference_payload(raw, mode=INFERENCE_MODE_STRICT)
        row = validated.record
        if not validated.ok:
            mark_state(proposal_source(raw, row), "quarantined")
            write_quarantine(
                Path(root),
                row,
                reasons=list(validated.quarantine_reasons),
                warnings=list(validated.warnings),
                original_payload=raw,
                session_id=_clean_str(session_id),
            )
            quarantined += 1
            continue

        src = _clean_str(row.get("source_bead"))
        tgt = _clean_str(row.get("target_bead"))
        if src not in beads or tgt not in beads:
            mark_state(src or proposal_source(raw, row), "quarantined")
            write_quarantine(
                Path(root),
                row,
                reasons=["bead_not_found"],
                warnings=[],
                original_payload=raw,
                session_id=_clean_str(session_id),
            )
            quarantined += 1
            continue
        accepted += 1
        out = _write_association_if_missing(
            root,
            source=src,
            target=tgt,
            relationship=_clean_str(row.get("relationship")),
            confidence=float(row.get("confidence") or 0.0),
            reason_text=_clean_str(row.get("reason_text")),
            edge_class="agent_judged",
            provenance=_clean_str(row.get("provenance")) or "model_inferred",
            reason_code=_clean_str(row.get("reason_code")),
            evidence_bead_ids=list(row.get("evidence_bead_ids") or []),
            evidence_refs=list(row.get("evidence_refs") or []),
            judge_model=_clean_str(row.get("judge_model")),
            prompt_version=_clean_str(row.get("prompt_version")),
            rubric_version=_clean_str(row.get("rubric_version")),
            grounding_hash=_clean_str(row.get("grounding_hash")),
            truth_basis=_clean_str(raw.get("truth_basis") or row.get("truth_basis")) or "host_reviewed_proposal",
            candidate_ids=[_clean_str(raw.get("candidate_id"))] if _clean_str(raw.get("candidate_id")) else [],
            association_run_id=_clean_str(run_id),
            association_policy_version=POLICY_VERSION,
        )
        if out.get("ok") and out.get("deduped"):
            deduped += 1
            mark_state(src, "linked")
        elif out.get("ok"):
            appended += 1
            mark_state(src, "linked")
            if _clean_str(out.get("association_id")):
                association_ids.append(_clean_str(out.get("association_id")))
        else:
            mark_state(src, "failed")
            errors.append({"edge": row, "error": out.get("error")})

    if _clean_str(run_id):
        _append_run_record(
            root,
            {
                "run_id": _clean_str(run_id),
                "status": "completed" if not errors else "failed",
                "trigger": "operator",
                "policy_version": POLICY_VERSION,
                "session_id": _clean_str(session_id),
                "bead_ids": list(state_by_bead),
                "association_state_by_bead": state_by_bead,
                "association_ids": association_ids,
                "counts": {
                    "accepted": accepted,
                    "appended": appended,
                    "deduped": deduped,
                    "quarantined": quarantined,
                    "failed": len(errors),
                },
                "errors": errors,
                "contract": "memory.association_proposals.v1",
            },
        )

    return {
        "ok": len(errors) == 0,
        "contract": "memory.association_proposals.v1",
        "accepted": accepted,
        "appended": appended,
        "deduped": deduped,
        "quarantined": quarantined,
        "association_ids": association_ids,
        "quarantine_path": str(Path(root) / ".beads" / "events" / "association-quarantine.jsonl"),
        "errors": errors,
    }


__all__ = [
    "ASSOCIATION_RUNS_SCHEMA",
    "POLICY_VERSION",
    "apply_association_proposals",
    "enqueue_association_coverage",
    "get_association_run",
    "latest_association_coverage",
    "on_bead_committed",
    "run_association_coverage",
]
