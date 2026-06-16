from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.association.quarantine import write_quarantine
from core_memory.persistence import events
from core_memory.persistence.io_utils import append_jsonl, store_lock
from core_memory.persistence.store import MemoryStore
from core_memory.persistence.store_relationship_ops import _mirror_association_to_graph
from core_memory.policy.association_contract import assoc_dedupe_key
from core_memory.policy.association_inference_v21 import (
    INFERENCE_MODE_STRICT,
    validate_and_normalize_inference_payload,
)
from core_memory.retrieval.lifecycle import mark_trace_dirty


ASSOCIATION_RUNS_SCHEMA = "core_memory.association_runs.v1"
POLICY_VERSION = "bead_association.v1"
DEFAULT_MAX_CANDIDATES = 40
ALLOWED_TRIGGERS = {
    "pre_commit",
    "post_commit",
    "session_flush",
    "operator",
    "periodic_transcript_push",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _events_dir(root: str | Path) -> Path:
    path = Path(root) / ".beads" / "events"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _runs_path(root: str | Path) -> Path:
    return _events_dir(root) / "association-runs.jsonl"


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


def _edge(
    source: str,
    target: str,
    relationship: str,
    *,
    reason_text: str,
    reason_code: str,
    confidence: float = 0.95,
) -> dict[str, Any]:
    return {
        "source_bead": source,
        "target_bead": target,
        "relationship": relationship,
        "confidence": float(confidence),
        "reason_text": reason_text,
        "rationale": reason_text,
        "edge_class": "system_structural",
        "provenance": "system_structural",
        "reason_code": reason_code,
    }


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

    same_session.sort(reverse=True)
    for _created, cid in same_session[:10]:
        _add_candidate(candidates, cid, self_id=bead_id, beads=beads)
    return candidates[: max(1, int(max_candidates))]


def _deterministic_edges_for_bead(
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
    edges: list[dict[str, Any]] = []

    prev_id = _clean_str(bead.get("prev_bead_id"))
    if prev_id in beads:
        edges.append(
            _edge(
                bead_id,
                prev_id,
                "follows",
                reason_text="Source bead follows the previous bead in the same session.",
                reason_code="session_temporal_adjacency",
                confidence=0.98,
            )
        )

    for target in _as_list(bead.get("supersedes")):
        target_id = _clean_str(target)
        if target_id in beads:
            edges.append(
                _edge(
                    bead_id,
                    target_id,
                    "supersedes",
                    reason_text="Source bead explicitly supersedes the target bead.",
                    reason_code="explicit_supersedes_field",
                    confidence=0.98,
                )
            )

    derived_ids = []
    for target in _as_list(bead.get("derived_from_bead_ids")):
        if _clean_str(target) in beads:
            derived_ids.append(_clean_str(target))
    derived_ids.extend(_resolve_reference_ids(index, _as_list(bead.get("derived_from"))))
    for target_id in dict.fromkeys(derived_ids):
        if target_id and target_id != bead_id:
            edges.append(
                _edge(
                    bead_id,
                    target_id,
                    "derived_from",
                    reason_text="Source bead declares the target bead as direct evidence.",
                    reason_code="explicit_derived_from",
                    confidence=0.95,
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
                edges.append(
                    _edge(
                        bead_id,
                        target_id,
                        "part_of",
                        reason_text="Section-scoped document bead belongs to the whole-document bead.",
                        reason_code="document_section_part_of_document",
                        confidence=0.98,
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
            edges.append(
                _edge(
                    bead_id,
                    target_id,
                    "associated_with",
                    reason_text="Both beads share a stable cross-source unifying id.",
                    reason_code="shared_core_memory_unifying_id",
                    confidence=0.9,
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
                edges.append(
                    _edge(
                        bead_id,
                        target_id,
                        "associated_with",
                        reason_text="Both beads refer to the same source object.",
                        reason_code="same_source_object",
                        confidence=0.88,
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
            edges.append(
                _edge(
                    bead_id,
                    matches[0][1],
                    "follows",
                    reason_text="Periodic transcript bead follows the prior snapshot for the same transcript.",
                    reason_code="periodic_transcript_snapshot_continuity",
                    confidence=0.9,
                )
            )

    return _dedupe_edge_rows(edges)


def _dedupe_edge_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = (
            _clean_str(row.get("source_bead")),
            _clean_str(row.get("target_bead")),
            _clean_str(row.get("relationship")).lower(),
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
) -> dict[str, Any]:
    index = _load_index(root)
    resolved_ids = [_clean_str(x) for x in (bead_ids or []) if _clean_str(x)]
    if not resolved_ids and _clean_str(session_id):
        resolved_ids = _resolve_session_bead_ids(index, session_id)
    resolved_ids = [bid for bid in dict.fromkeys(resolved_ids) if bid in (index.get("beads") or {})]
    if not resolved_ids:
        return {
            "ok": False,
            "error": "association_run_requires_bead_ids_or_session_id",
            "contract": "memory.association_run.v1",
        }

    run_id = f"arun-{uuid.uuid4().hex[:12]}"
    trigger_n = _normalize_trigger(trigger)
    initial_states = {bid: "deferred" for bid in resolved_ids}
    if run_inline:
        return run_association_coverage(
            root=root,
            run_id=run_id,
            bead_ids=resolved_ids,
            session_id=session_id,
            trigger=trigger_n,
            candidate_bead_ids=list(candidate_bead_ids or []),
            max_candidates=max_candidates,
            policy_version=policy_version,
        )

    idempotency_key = (
        f"assoc:{policy_version}:session:{session_id}"
        if _clean_str(session_id)
        else f"assoc:{policy_version}:beads:{'-'.join(resolved_ids)}"
    )
    from core_memory.runtime.queue.side_effect_queue import enqueue_side_effect_event

    queue = enqueue_side_effect_event(
        root=root,
        kind="association-pass",
        payload={
            "run_id": run_id,
            "bead_ids": resolved_ids,
            "session_id": _clean_str(session_id),
            "trigger": trigger_n,
            "candidate_bead_ids": list(candidate_bead_ids or []),
            "max_candidates": max(1, int(max_candidates)),
            "policy_version": _clean_str(policy_version) or POLICY_VERSION,
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
            "session_id": _clean_str(session_id),
            "bead_ids": resolved_ids,
            "association_state_by_bead": initial_states,
            "queued_job_id": _clean_str(queue.get("id")),
            "queue": queue,
            "counts": {"appended": 0, "deduped": 0, "quarantined": 0, "failed": 0},
            "contract": "memory.association_run.v1",
        },
    )
    return {
        "ok": bool(queue.get("ok")),
        "contract": "memory.association_run.v1",
        "run_id": run_id,
        "status": "queued" if queue.get("ok") else "failed",
        "bead_ids": resolved_ids,
        "association_state_by_bead": initial_states,
        "queued_job_id": _clean_str(queue.get("id")),
        "association_queued": bool(queue.get("ok")),
        "queue": queue,
    }


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
) -> dict[str, Any]:
    run_id_final = _clean_str(run_id) or f"arun-{uuid.uuid4().hex[:12]}"
    trigger_n = _normalize_trigger(trigger)
    index = _load_index(root)
    beads = index.get("beads") or {}
    resolved_ids = [_clean_str(x) for x in (bead_ids or []) if _clean_str(x)]
    if not resolved_ids and _clean_str(session_id):
        resolved_ids = _resolve_session_bead_ids(index, session_id)
    resolved_ids = [bid for bid in dict.fromkeys(resolved_ids) if bid in beads and _coverage_eligible(beads.get(bid))]
    if not resolved_ids:
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

    _append_run_record(
        root,
        {
            "run_id": run_id_final,
            "status": "running",
            "trigger": trigger_n,
            "policy_version": _clean_str(policy_version) or POLICY_VERSION,
            "session_id": _clean_str(session_id),
            "bead_ids": resolved_ids,
            "association_state_by_bead": {bid: "deferred" for bid in resolved_ids},
            "counts": {"appended": 0, "deduped": 0, "quarantined": 0, "failed": 0},
            "contract": "memory.association_run.v1",
        },
    )

    appended = 0
    deduped = 0
    failed = 0
    association_ids: list[str] = []
    state_by_bead: dict[str, str] = {}
    errors: list[dict[str, Any]] = []
    explicit_candidates = [_clean_str(x) for x in (candidate_bead_ids or []) if _clean_str(x)]

    for bead_id in resolved_ids:
        bead = beads.get(bead_id)
        if not isinstance(bead, dict):
            state_by_bead[bead_id] = "failed"
            failed += 1
            continue
        edges = _deterministic_edges_for_bead(
            index,
            bead,
            explicit_candidate_ids=explicit_candidates,
            max_candidates=max_candidates,
        )
        bead_had_link = False
        for row in edges:
            try:
                out = _write_association_if_missing(
                    root,
                    source=_clean_str(row.get("source_bead")),
                    target=_clean_str(row.get("target_bead")),
                    relationship=_clean_str(row.get("relationship")),
                    confidence=float(row.get("confidence") or 0.0),
                    reason_text=_clean_str(row.get("reason_text")),
                    edge_class=_clean_str(row.get("edge_class")),
                    provenance=_clean_str(row.get("provenance")),
                    reason_code=_clean_str(row.get("reason_code")),
                )
            except Exception as exc:
                out = {"ok": False, "error": str(exc)}
            if out.get("ok") and out.get("deduped"):
                deduped += 1
                bead_had_link = True
            elif out.get("ok"):
                appended += 1
                bead_had_link = True
                if _clean_str(out.get("association_id")):
                    association_ids.append(_clean_str(out.get("association_id")))
            else:
                failed += 1
                errors.append({"bead_id": bead_id, "edge": row, "error": out.get("error")})
        state_by_bead[bead_id] = "linked" if bead_had_link else "no_supported_links"

    status = "completed" if failed == 0 else "failed"
    counts = {"appended": appended, "deduped": deduped, "quarantined": 0, "failed": failed}
    out = {
        "ok": failed == 0,
        "contract": "memory.association_run.v1",
        "run_id": run_id_final,
        "status": status,
        "trigger": trigger_n,
        "policy_version": _clean_str(policy_version) or POLICY_VERSION,
        "session_id": _clean_str(session_id),
        "bead_ids": resolved_ids,
        "association_state_by_bead": state_by_bead,
        "association_ids": association_ids,
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

    for raw in list(associations or []):
        if not isinstance(raw, dict):
            quarantined += 1
            continue
        validated = validate_and_normalize_inference_payload(raw, mode=INFERENCE_MODE_STRICT)
        row = validated.record
        if not validated.ok:
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
        )
        if out.get("ok") and out.get("deduped"):
            deduped += 1
        elif out.get("ok"):
            appended += 1
            if _clean_str(out.get("association_id")):
                association_ids.append(_clean_str(out.get("association_id")))
        else:
            errors.append({"edge": row, "error": out.get("error")})

    if _clean_str(run_id):
        state_by_bead: dict[str, str] = {}
        for assoc in list(associations or []):
            if isinstance(assoc, dict):
                src = _clean_str(assoc.get("source_bead") or assoc.get("source_bead_id"))
                if src:
                    state_by_bead[src] = "linked" if appended or deduped else ("quarantined" if quarantined else "failed")
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
    "run_association_coverage",
]
