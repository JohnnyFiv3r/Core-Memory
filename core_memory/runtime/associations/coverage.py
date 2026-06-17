from __future__ import annotations

import hashlib
import json
import os
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
ASSOCIATION_CANDIDATES_SCHEMA = "core_memory.association_candidates.v1"
ASSOCIATION_JUDGE_DECISIONS_SCHEMA = "core_memory.association_judge_decisions.v1"
ASSOCIATION_COVERAGE_SUMMARY_CONTRACT = "memory.association_coverage_summary.v1"
ASSOCIATION_CANDIDATES_CONTRACT = "memory.association_candidates.v1"
ASSOCIATION_CANDIDATE_DECISION_CONTRACT = "memory.association_candidate_decision.v1"
ASSOCIATION_JUDGE_CONTRACT = "memory.association_judge.v1"
POLICY_VERSION = "bead_association.v1"
JUDGE_PROMPT_VERSION = "association_judge.v1"
JUDGE_RUBRIC_VERSION = "association_truth.v1"
CANDIDATE_GENERATION_VERSION = "association_candidates.v1"
DEFAULT_MAX_CANDIDATES = 40
DEFAULT_ASSOCIATION_JUDGE_MIN_OUTPUT_TOKENS = 1800
DEFAULT_ASSOCIATION_JUDGE_MAX_OUTPUT_TOKENS = 6000
DEFAULT_ASSOCIATION_JUDGE_OUTPUT_TOKENS_PER_CANDIDATE = 220
DEFAULT_ASSOCIATION_JUDGE_OUTPUT_TOKENS_PER_SOURCE_BEAD = 40
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
JUDGE_ACTION_ALIASES = {
    "approve": "accept",
    "approved": "accept",
    "associate": "accept",
    "link": "accept",
    "linked": "accept",
    "supported": "accept",
    "create": "add",
    "create_edge": "add",
    "add_link": "add",
    "no link": "no_link",
    "no-link": "no_link",
    "no_supported_link": "no_link",
    "no_supported_links": "no_link",
    "none": "no_link",
    "not_supported": "no_link",
    "unsupported": "no_link",
    "reject_candidate": "reject",
    "rejected": "reject",
}
ACTIVE_ASSOCIATION_STATUSES = {"", "active", "current", "linked"}


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


def _bead_set_signature(bead_ids: list[str]) -> str:
    normalized = sorted({_clean_str(x) for x in bead_ids if _clean_str(x)})
    material = "\n".join(normalized)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


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


def _read_jsonl_dicts(path: Path) -> list[dict[str, Any]]:
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


def _iter_candidate_records(root: str | Path) -> list[dict[str, Any]]:
    return _read_jsonl_dicts(_candidates_path(root))


def _iter_judge_decision_records(root: str | Path) -> list[dict[str, Any]]:
    return _read_jsonl_dicts(_judge_decisions_path(root))


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


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = _clean_str(row.get(key)).lower() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _active_associations(index: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for assoc in index.get("associations") or []:
        if not isinstance(assoc, dict):
            continue
        status = _clean_str(assoc.get("status")).lower()
        if status in ACTIVE_ASSOCIATION_STATUSES:
            out.append(assoc)
    return out


def _eligible_bead_ids(index: dict[str, Any]) -> list[str]:
    rows: list[tuple[str, str]] = []
    for bid, bead in (index.get("beads") or {}).items():
        if not isinstance(bead, dict):
            continue
        bead_id = _clean_str(bead.get("id") or bid)
        if not bead_id:
            continue
        if _coverage_eligible({**bead, "id": bead_id}):
            rows.append((_clean_str(bead.get("created_at")), bead_id))
    rows.sort()
    return [bid for _created, bid in rows]


def _isolated_eligible_bead_ids(index: dict[str, Any]) -> list[str]:
    eligible = _eligible_bead_ids(index)
    associated: set[str] = set()
    for assoc in _active_associations(index):
        source = _clean_str(assoc.get("source_bead") or assoc.get("source_bead_id"))
        target = _clean_str(assoc.get("target_bead") or assoc.get("target_bead_id"))
        if source:
            associated.add(source)
        if target:
            associated.add(target)
    return [bid for bid in eligible if bid not in associated]


def _candidate_status_for_action(action: str) -> str:
    normalized = _clean_str(action).lower()
    if normalized in {"accept", "modify", "invert", "replace", "add"}:
        return "linked"
    if normalized in {"reject", "no_link"}:
        return "no_supported_links"
    return ""


def _candidate_observations(root: str | Path) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for record in _iter_candidate_records(root):
        base = {
            "run_id": _clean_str(record.get("run_id")),
            "trigger": _clean_str(record.get("trigger")),
            "policy_version": _clean_str(record.get("policy_version")),
            "prompt_version": _clean_str(record.get("prompt_version")),
            "rubric_version": _clean_str(record.get("rubric_version")),
            "graph_revision": _clean_str(record.get("graph_revision")),
            "recorded_at": _clean_str(record.get("recorded_at")),
            "candidate_generation_version": _clean_str(record.get("candidate_generation_version")),
        }
        candidates = [x for x in (record.get("candidates") or []) if isinstance(x, dict)]
        for candidate in candidates:
            row = {**base, **candidate}
            row.setdefault("status", "pending_judge")
            observations.append(row)
        if _clean_str(record.get("candidate_id")):
            row = {**record}
            row.pop("candidates", None)
            row.setdefault("status", "pending_judge")
            observations.append(row)
    return observations


def _candidate_status_updates(root: str | Path) -> dict[str, dict[str, Any]]:
    updates: dict[str, dict[str, Any]] = {}

    def update(candidate_id: str, status: str, row: dict[str, Any]) -> None:
        cid = _clean_str(candidate_id)
        state = _clean_str(status).lower()
        if not cid or not state:
            return
        updates[cid] = {
            "status": state,
            "recorded_at": _clean_str(row.get("recorded_at")),
            "run_id": _clean_str(row.get("run_id")),
            "decision": dict(row.get("decision") or {}) if isinstance(row.get("decision"), dict) else None,
        }

    for record in _iter_judge_decision_records(root):
        record_status = _clean_str(record.get("status")).lower()
        if record_status in {"pending_judge", "judge_failed", "coverage_failed"}:
            for cid in record.get("candidate_ids") or []:
                update(_clean_str(cid), record_status, record)
        for decision in [x for x in (record.get("decisions") or []) if isinstance(x, dict)]:
            status = _candidate_status_for_action(_clean_str(decision.get("action")))
            if status:
                update(_clean_str(decision.get("candidate_id")), status, {**record, "decision": decision})

    for record in _iter_candidate_records(root):
        cid = _clean_str(record.get("candidate_id"))
        status = _clean_str(record.get("status")).lower()
        if cid and status:
            update(cid, status, record)
    return updates


def _normalized_candidate_rows(root: str | Path) -> tuple[list[dict[str, Any]], int]:
    index = _load_index(root)
    beads = index.get("beads") or {}
    observations = _candidate_observations(root)
    status_updates = _candidate_status_updates(root)
    by_id: dict[str, dict[str, Any]] = {}

    for observation in observations:
        candidate_id = _clean_str(observation.get("candidate_id"))
        if not candidate_id:
            continue
        existing = by_id.get(candidate_id)
        row = dict(existing or {})
        if not row:
            row = dict(observation)
            row["first_seen_at"] = _clean_str(observation.get("recorded_at"))
            row["rediscovery_count"] = 0
            row["rediscovery_observations"] = []
            row["run_ids"] = []
            row["triggers"] = []
            row["trigger_counts"] = {}

        row["rediscovery_count"] = int(row.get("rediscovery_count") or 0) + 1
        row["latest_seen_at"] = _clean_str(observation.get("recorded_at")) or _clean_str(row.get("latest_seen_at"))
        if _clean_str(observation.get("recorded_at")):
            row["recorded_at"] = _clean_str(observation.get("recorded_at"))
        for key, value in observation.items():
            if value not in (None, "", [], {}) and key not in {"rediscovery_observations"}:
                row[key] = value
        run_id = _clean_str(observation.get("run_id"))
        if run_id and run_id not in row["run_ids"]:
            row["run_ids"].append(run_id)
        trigger = _clean_str(observation.get("trigger"))
        if trigger and trigger not in row["triggers"]:
            row["triggers"].append(trigger)
        if trigger:
            counts = row["trigger_counts"]
            counts[trigger] = int(counts.get(trigger) or 0) + 1
        row["rediscovery_observations"].append({
            "run_id": run_id,
            "trigger": trigger,
            "recorded_at": _clean_str(observation.get("recorded_at")),
        })
        by_id[candidate_id] = row

    for candidate_id, update in status_updates.items():
        row = by_id.get(candidate_id)
        if row is None:
            row = {
                "candidate_id": candidate_id,
                "first_seen_at": _clean_str(update.get("recorded_at")),
                "latest_seen_at": _clean_str(update.get("recorded_at")),
                "recorded_at": _clean_str(update.get("recorded_at")),
                "run_ids": [],
                "triggers": [],
                "trigger_counts": {},
                "rediscovery_count": 0,
                "rediscovery_observations": [],
            }
        row["status"] = _clean_str(update.get("status")) or "pending_judge"
        if _clean_str(update.get("run_id")) and _clean_str(update.get("run_id")) not in row["run_ids"]:
            row["run_ids"].append(_clean_str(update.get("run_id")))
        by_id[candidate_id] = row

    for row in by_id.values():
        row.setdefault("status", "pending_judge")
        source = _clean_str(row.get("source_bead"))
        target = _clean_str(row.get("target_bead"))
        source_bead = beads.get(source) if source else None
        target_bead = beads.get(target) if target else None
        if isinstance(source_bead, dict):
            row.setdefault("source_title", _clean_str(source_bead.get("title")) or source)
            row.setdefault("source_type", _clean_str(source_bead.get("type")))
        if isinstance(target_bead, dict):
            row.setdefault("target_title", _clean_str(target_bead.get("title")) or target)
            row.setdefault("target_type", _clean_str(target_bead.get("type")))

    rows = list(by_id.values())
    rows.sort(key=lambda item: _clean_str(item.get("latest_seen_at") or item.get("recorded_at")), reverse=True)
    return rows, len(observations)


def list_association_candidates(
    root: str | Path,
    *,
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    rows, observation_count = _normalized_candidate_rows(root)
    status_n = _clean_str(status).lower()
    if status_n:
        rows = [row for row in rows if _clean_str(row.get("status")).lower() == status_n]
    limit_n = max(1, int(limit))
    return {
        "ok": True,
        "contract": ASSOCIATION_CANDIDATES_CONTRACT,
        "status": status_n or None,
        "count": len(rows),
        "observation_count": observation_count,
        "results": rows[:limit_n],
    }


def association_coverage_summary(
    root: str | Path,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    index = _load_index(root)
    eligible = _eligible_bead_ids(index)
    isolated = _isolated_eligible_bead_ids(index)
    active_associations = _active_associations(index)
    candidates, observation_count = _normalized_candidate_rows(root)
    runs = _iter_run_records(root)
    latest_runs = sorted(
        [dict(row) for row in runs],
        key=lambda item: _clean_str(item.get("recorded_at") or item.get("created_at")),
        reverse=True,
    )[: max(1, int(limit))]
    candidate_status_counts = _count_by(candidates, "status")
    pending_judge_count = int(candidate_status_counts.get("pending_judge") or 0)
    return {
        "ok": True,
        "contract": ASSOCIATION_COVERAGE_SUMMARY_CONTRACT,
        "graph_revision": _graph_revision(index),
        "eligible_bead_count": len(eligible),
        "active_association_count": len(active_associations),
        "isolated_eligible_bead_count": len(isolated),
        "isolated_eligible_bead_ids": isolated[: max(1, int(limit))],
        "candidate_count": len(candidates),
        "candidate_observation_count": observation_count,
        "candidate_status_counts": candidate_status_counts,
        "pending_judge_count": pending_judge_count,
        "runs_by_status": _count_by(runs, "status"),
        "runs_by_trigger": _count_by(runs, "trigger"),
        "latest_runs": latest_runs,
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


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name) or "").strip() or default)
    except (TypeError, ValueError):
        return default


def _association_judge_max_tokens(context: dict[str, Any]) -> int:
    candidates = len([x for x in (context.get("candidates") or []) if isinstance(x, dict)])
    source_beads = len([x for x in (context.get("source_bead_ids") or []) if _clean_str(x)])
    minimum = max(1, _env_int("CORE_MEMORY_ASSOCIATION_JUDGE_MIN_OUTPUT_TOKENS", DEFAULT_ASSOCIATION_JUDGE_MIN_OUTPUT_TOKENS))
    maximum = max(minimum, _env_int("CORE_MEMORY_ASSOCIATION_JUDGE_MAX_OUTPUT_TOKENS", DEFAULT_ASSOCIATION_JUDGE_MAX_OUTPUT_TOKENS))
    per_candidate = max(
        1,
        _env_int(
            "CORE_MEMORY_ASSOCIATION_JUDGE_OUTPUT_TOKENS_PER_CANDIDATE",
            DEFAULT_ASSOCIATION_JUDGE_OUTPUT_TOKENS_PER_CANDIDATE,
        ),
    )
    per_source = max(
        0,
        _env_int(
            "CORE_MEMORY_ASSOCIATION_JUDGE_OUTPUT_TOKENS_PER_SOURCE_BEAD",
            DEFAULT_ASSOCIATION_JUDGE_OUTPUT_TOKENS_PER_SOURCE_BEAD,
        ),
    )
    requested = minimum + candidates * per_candidate + source_beads * per_source
    return min(maximum, max(minimum, requested))


def _parse_association_judge_json(text: str) -> dict[str, Any]:
    raw = _clean_str(text)
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].lstrip("`").strip().lower() in {"json", "javascript", "js"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            obj = json.loads(raw[start : end + 1])
        else:
            raise
    if not isinstance(obj, dict):
        raise ValueError("association_judge_returned_non_object")
    return obj


class LLMAssociationJudge:
    def review(self, context: dict[str, Any]) -> dict[str, Any]:
        from core_memory.llm_client import chat_complete

        prompt = (
            "You are Core Memory's association judge. Review candidate bead associations "
            "against the supplied bead content, metadata, provenance, and evidence refs. "
            "Return only JSON matching contract memory.association_judge.v1. "
            "For every supported or unsupported candidate, emit one decisions[] object with "
            "candidate_id, action, reason_text, and truth_basis. Allowed decision actions are "
            "only: accept, reject, modify, invert, replace, add, no_link. Do not use linked or "
            "no_supported_links as action values; those are reviewed_beads association_state "
            "values only. Do not approve unsupported links.\n\n"
            f"{json.dumps(context, ensure_ascii=False, sort_keys=True)}"
        )
        max_tokens = _association_judge_max_tokens(context)
        try:
            raw = chat_complete(prompt, max_tokens=max_tokens, temperature=0, json_mode=True)
        except Exception:
            raw = chat_complete(prompt, max_tokens=max_tokens, temperature=0)
        return _parse_association_judge_json(raw)


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


def _judge_action(decision: dict[str, Any]) -> str:
    raw = _clean_str(
        decision.get("action")
        or decision.get("decision")
        or decision.get("association_state")
        or decision.get("status"),
    ).lower()
    return JUDGE_ACTION_ALIASES.get(raw, raw)


def _bead_context(bead: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id", "type", "title", "summary", "detail", "session_id", "source_turn_ids",
        "tags", "entities", "topics", "created_at", "prev_bead_id", "next_bead_id",
        "source_id", "source_event_id", "source_system", "source_kind",
        "core_memory_unifying_id", "document_id", "ragie_document_id",
        "raw_source_object_id", "section_refs", "source_record_id",
        "business_object_id", "transcript_id", "conversation_id", "supersedes",
        "derived_from", "derived_from_bead_ids", "evidence_refs",
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
    action = _judge_action(decision)
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
        "reason_code": _clean_str(decision.get("reason_code") or (candidate or {}).get("reason_code")),
        "evidence_bead_ids": evidence_bead_ids,
        "evidence_refs": evidence_refs,
        "truth_basis": _clean_str(decision.get("truth_basis")) or "association_judge_candidate_review",
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
        action = _judge_action(decision)
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


def _select_sweep_bead_ids(
    root: str | Path,
    index: dict[str, Any],
    *,
    sweep_mode: str | None = None,
    sweep_cursor: str | None = None,
    sweep_limit: int | None = None,
) -> dict[str, Any]:
    mode = _clean_str(sweep_mode).lower() or "incomplete"
    if mode not in {"all", "isolated", "incomplete"}:
        mode = "incomplete"
    limit_n = max(1, int(sweep_limit or DEFAULT_MAX_CANDIDATES))
    eligible = _eligible_bead_ids(index)
    isolated = _isolated_eligible_bead_ids(index)

    if mode == "all":
        selected_pool = eligible
    elif mode == "isolated":
        selected_pool = isolated
    else:
        incomplete: list[str] = []
        for bead_id in eligible:
            state = _clean_str(latest_association_coverage(root, bead_id).get("state")).lower()
            if state in {"", "unknown"} or state in INCOMPLETE_STATES:
                incomplete.append(bead_id)
        selected_pool = list(dict.fromkeys([*isolated, *incomplete]))

    cursor = _clean_str(sweep_cursor)
    start_index = 0
    if cursor and cursor in selected_pool:
        start_index = selected_pool.index(cursor) + 1
    selected = selected_pool[start_index : start_index + limit_n]
    remaining_after = selected_pool[start_index + limit_n :]
    return {
        "sweep": True,
        "sweep_mode": mode,
        "sweep_cursor": cursor or None,
        "sweep_limit": limit_n,
        "selected_bead_ids": selected,
        "next_sweep_cursor": selected[-1] if remaining_after and selected else None,
        "sweep_complete": not remaining_after,
        "eligible_bead_count": len(eligible),
        "isolated_eligible_bead_count": len(isolated),
    }


def _with_sweep_fields(out: dict[str, Any], sweep_info: dict[str, Any] | None) -> dict[str, Any]:
    if not sweep_info:
        return out
    for key in ("sweep", "sweep_mode", "sweep_cursor", "sweep_limit", "next_sweep_cursor", "sweep_complete"):
        out[key] = sweep_info.get(key)
    return out


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
    sweep: bool = False,
    sweep_mode: str | None = None,
    sweep_cursor: str | None = None,
    sweep_limit: int | None = None,
) -> dict[str, Any]:
    index = _load_index(root)
    resolved_ids = [_clean_str(x) for x in (bead_ids or []) if _clean_str(x)]
    if not resolved_ids and _clean_str(session_id):
        resolved_ids = _resolve_session_bead_ids(index, session_id)
    sweep_info: dict[str, Any] | None = None
    if bool(sweep) and not resolved_ids:
        sweep_info = _select_sweep_bead_ids(
            root,
            index,
            sweep_mode=sweep_mode,
            sweep_cursor=sweep_cursor,
            sweep_limit=sweep_limit,
        )
        resolved_ids = list(sweep_info.get("selected_bead_ids") or [])
    resolved_ids = [bid for bid in dict.fromkeys(resolved_ids) if bid in (index.get("beads") or {})]
    skipped_ids = [bid for bid in resolved_ids if not _coverage_eligible((index.get("beads") or {}).get(bid))]
    eligible_ids = [bid for bid in resolved_ids if bid not in set(skipped_ids)]
    if not resolved_ids:
        if sweep_info is not None:
            run_id = f"arun-{uuid.uuid4().hex[:12]}"
            out = {
                "ok": True,
                "error": None,
                "contract": "memory.association_run.v1",
                "run_id": run_id,
                "status": "completed",
                "trigger": _normalize_trigger(trigger),
                "policy_version": _clean_str(policy_version) or POLICY_VERSION,
                "prompt_version": _clean_str(prompt_version) or JUDGE_PROMPT_VERSION,
                "rubric_version": _clean_str(rubric_version) or JUDGE_RUBRIC_VERSION,
                "graph_revision": _clean_str(graph_revision) or _graph_revision(index),
                "session_id": _clean_str(session_id),
                "bead_ids": [],
                "skipped_bead_ids": [],
                "association_state_by_bead": {},
                "counts": {"appended": 0, "deduped": 0, "quarantined": 0, "failed": 0, "skipped": 0},
            }
            _with_sweep_fields(out, sweep_info)
            _append_run_record(root, out)
            return out
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
        _with_sweep_fields(out, sweep_info)
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
            sweep_info=sweep_info,
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
            "sweep": bool((sweep_info or {}).get("sweep")),
            "sweep_mode": (sweep_info or {}).get("sweep_mode"),
            "sweep_cursor": (sweep_info or {}).get("sweep_cursor"),
            "sweep_limit": (sweep_info or {}).get("sweep_limit"),
            "next_sweep_cursor": (sweep_info or {}).get("next_sweep_cursor"),
            "sweep_complete": (sweep_info or {}).get("sweep_complete"),
        },
        idempotency_key=idempotency_key,
    )
    queued_record = {
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
    }
    _with_sweep_fields(queued_record, sweep_info)
    _append_run_record(root, queued_record)
    queued_out = {
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
    _with_sweep_fields(queued_out, sweep_info)
    return queued_out


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
    sweep_info: dict[str, Any] | None = None,
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
        _with_sweep_fields(out, sweep_info)
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
        _with_sweep_fields(out, sweep_info)
        _append_run_record(root, out)
        return out

    pending_states = {bid: "pending_judge" for bid in eligible_ids}
    pending_states.update({bid: "skipped_ineligible" for bid in skipped_ids})
    running_record = {
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
    }
    _with_sweep_fields(running_record, sweep_info)
    _append_run_record(root, running_record)

    explicit_candidates = [_clean_str(x) for x in (candidate_bead_ids or []) if _clean_str(x)]
    candidates: list[dict[str, Any]] = []
    candidate_state = {bid: "pending_judge" for bid in eligible_ids}
    candidate_state.update({bid: "skipped_ineligible" for bid in skipped_ids})

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
    candidate_source_ids = {
        _clean_str(candidate.get("source_bead"))
        for candidate in candidates
        if isinstance(candidate, dict) and _clean_str(candidate.get("source_bead"))
    }
    no_candidate_ids = [bid for bid in eligible_ids if bid not in candidate_source_ids]
    judge_source_ids = [bid for bid in eligible_ids if bid in candidate_source_ids]
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
            "source_bead_ids": eligible_ids,
            "no_candidate_source_bead_ids": no_candidate_ids,
            "explicit_candidate_bead_ids": explicit_candidates,
            "max_candidates": max(1, int(max_candidates)),
            "candidate_count": len(candidates),
            "candidates": candidates,
        },
    )

    if not candidates:
        state_by_bead = {bid: "no_supported_links" for bid in eligible_ids}
        state_by_bead.update({bid: "skipped_ineligible" for bid in skipped_ids})
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
            "bead_ids": eligible_ids,
            "skipped_bead_ids": skipped_ids,
            "association_state_by_bead": state_by_bead,
            "candidate_count": 0,
            "counts": {
                "appended": 0,
                "deduped": 0,
                "quarantined": 0,
                "failed": 0,
                "pending_judge": 0,
                "no_supported_links": len(eligible_ids),
                "skipped": len(skipped_ids),
                "candidates": 0,
            },
        }
        _with_sweep_fields(out, sweep_info)
        _append_judge_decision_record(
            root,
            {
                "contract": ASSOCIATION_JUDGE_CONTRACT,
                "run_id": run_id_final,
                "status": "completed",
                "candidate_ids": [],
                "source_bead_ids": eligible_ids,
                "reviewed_beads": [
                    {"bead_id": bid, "association_state": "no_supported_links"}
                    for bid in eligible_ids
                ],
                "counts": {"no_supported_links": len(eligible_ids)},
                "reason": "no_candidate_proposals",
            },
        )
        _append_run_record(root, out)
        return out

    judge_context = _build_judge_context(
        index=index,
        run_id=run_id_final,
        trigger=trigger_n,
        source_bead_ids=judge_source_ids,
        candidates=candidates,
        policy_version=_clean_str(policy_version) or POLICY_VERSION,
        prompt_version=prompt_v,
        rubric_version=rubric_v,
    )
    try:
        judge_result = _invoke_association_judge(judge_context, judge=judge)
    except AssociationJudgeUnavailable as exc:
        state_by_bead = {bid: "pending_judge" for bid in judge_source_ids}
        state_by_bead.update({bid: "no_supported_links" for bid in no_candidate_ids})
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
                "pending_judge": len(judge_source_ids),
                "no_supported_links": len(no_candidate_ids),
                "skipped": len(skipped_ids),
            },
            "warning": _clean_str(exc),
        }
        _with_sweep_fields(out, sweep_info)
        _append_judge_decision_record(
            root,
            {
                "contract": ASSOCIATION_JUDGE_CONTRACT,
                "run_id": run_id_final,
                "status": "pending_judge",
                "warning": _clean_str(exc),
                "candidate_ids": [_clean_str(c.get("candidate_id")) for c in candidates],
                "source_bead_ids": eligible_ids,
                "no_candidate_source_bead_ids": no_candidate_ids,
            },
        )
        _append_run_record(root, out)
        return out
    except Exception as exc:
        state_by_bead = {bid: "judge_failed" for bid in judge_source_ids}
        state_by_bead.update({bid: "no_supported_links" for bid in no_candidate_ids})
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
            "counts": {
                "appended": 0,
                "deduped": 0,
                "quarantined": 0,
                "failed": len(judge_source_ids),
                "no_supported_links": len(no_candidate_ids),
                "skipped": len(skipped_ids),
            },
            "error": _clean_str(exc),
        }
        _with_sweep_fields(out, sweep_info)
        _append_judge_decision_record(
            root,
            {
                "contract": ASSOCIATION_JUDGE_CONTRACT,
                "run_id": run_id_final,
                "status": "judge_failed",
                "error": _clean_str(exc),
                "candidate_ids": [_clean_str(c.get("candidate_id")) for c in candidates],
                "source_bead_ids": eligible_ids,
                "no_candidate_source_bead_ids": no_candidate_ids,
            },
        )
        _append_run_record(root, out)
        return out

    applied = _apply_judge_result(
        root,
        index=index,
        run_id=run_id_final,
        session_id=session_id,
        source_bead_ids=judge_source_ids,
        candidates=candidates,
        judge_context=judge_context,
        judge_result=judge_result,
        policy_version=_clean_str(policy_version) or POLICY_VERSION,
        prompt_version=prompt_v,
        rubric_version=rubric_v,
    )
    state_by_bead = dict(applied.get("association_state_by_bead") or {})
    for bid in judge_source_ids:
        state_by_bead.setdefault(bid, "pending_judge")
    for bid in no_candidate_ids:
        state_by_bead.setdefault(bid, "no_supported_links")
    state_by_bead.update({bid: "skipped_ineligible" for bid in skipped_ids})
    failed = int(applied.get("failed") or 0)
    quarantined = int(applied.get("quarantined") or 0)
    unresolved_ids = [bid for bid in judge_source_ids if state_by_bead.get(bid) == "pending_judge"]
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
        "no_supported_links": len(no_candidate_ids),
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
    _with_sweep_fields(out, sweep_info)
    _append_run_record(root, out)
    return out


def decide_association_candidate(
    root: str | Path,
    *,
    candidate_id: str,
    action: str,
    run_id: str | None = None,
    session_id: str | None = None,
    reviewer: str | None = None,
    reason_text: str | None = None,
    truth_basis: str | None = None,
    confidence: float | None = None,
    relationship: str | None = None,
    source_bead: str | None = None,
    target_bead: str | None = None,
    evidence_refs: list[Any] | None = None,
    evidence_bead_ids: list[Any] | None = None,
    judge_model: str | None = None,
    prompt_version: str | None = None,
    rubric_version: str | None = None,
) -> dict[str, Any]:
    cid = _clean_str(candidate_id)
    action_n = _clean_str(action).lower()
    if not cid:
        return {"ok": False, "contract": ASSOCIATION_CANDIDATE_DECISION_CONTRACT, "error": "missing_candidate_id"}
    if action_n not in JUDGE_ACTIONS:
        return {
            "ok": False,
            "contract": ASSOCIATION_CANDIDATE_DECISION_CONTRACT,
            "candidate_id": cid,
            "error": "invalid_association_candidate_action",
        }

    candidates, _observation_count = _normalized_candidate_rows(root)
    candidate = next((row for row in candidates if _clean_str(row.get("candidate_id")) == cid), None)
    if candidate is None:
        return {
            "ok": False,
            "contract": ASSOCIATION_CANDIDATE_DECISION_CONTRACT,
            "candidate_id": cid,
            "error": "association_candidate_not_found",
        }

    index = _load_index(root)
    run_id_final = _clean_str(run_id) or _clean_str(candidate.get("run_id")) or f"arun-review-{uuid.uuid4().hex[:12]}"
    prompt_v = _clean_str(prompt_version) or _clean_str(candidate.get("prompt_version")) or JUDGE_PROMPT_VERSION
    rubric_v = _clean_str(rubric_version) or _clean_str(candidate.get("rubric_version")) or JUDGE_RUBRIC_VERSION
    source = _clean_str(source_bead) or _clean_str(candidate.get("source_bead"))
    target = _clean_str(target_bead) or _clean_str(candidate.get("target_bead"))
    rel = _clean_str(relationship) or _clean_str(candidate.get("proposed_relationship"))
    decision: dict[str, Any] = {
        "candidate_id": cid,
        "action": action_n,
        "source_bead": source,
        "target_bead": target,
        "relationship": rel,
        "reason_text": _clean_str(reason_text) or _clean_str(candidate.get("system_rationale")),
        "truth_basis": _clean_str(truth_basis) or "operator_review",
        "reviewer": _clean_str(reviewer),
        "evidence_refs": list(evidence_refs or candidate.get("evidence_refs") or []),
        "evidence_bead_ids": list(evidence_bead_ids or candidate.get("evidence_bead_ids") or []),
    }
    if confidence is not None:
        decision["confidence"] = float(confidence)
    elif candidate.get("confidence_prior") is not None:
        decision["confidence"] = float(candidate.get("confidence_prior") or 0.0)

    state = "no_supported_links" if action_n in {"reject", "no_link"} else "linked"
    judge_context = _build_judge_context(
        index=index,
        run_id=run_id_final,
        trigger="operator",
        source_bead_ids=[source] if source else [],
        candidates=[candidate],
        policy_version=POLICY_VERSION,
        prompt_version=prompt_v,
        rubric_version=rubric_v,
    )
    applied = _apply_judge_result(
        root,
        index=index,
        run_id=run_id_final,
        session_id=session_id,
        source_bead_ids=[source] if source else [],
        candidates=[candidate],
        judge_context=judge_context,
        judge_result={
            "contract": ASSOCIATION_JUDGE_CONTRACT,
            "run_id": run_id_final,
            "judge_model": _clean_str(judge_model) or "operator-review",
            "prompt_version": prompt_v,
            "rubric_version": rubric_v,
            "decisions": [decision],
            "reviewed_beads": [{"bead_id": source, "association_state": state}] if source else [],
        },
        policy_version=POLICY_VERSION,
        prompt_version=prompt_v,
        rubric_version=rubric_v,
    )
    failed = int(applied.get("failed") or 0) + len(applied.get("errors") or [])
    quarantined = int(applied.get("quarantined") or 0)
    final_status = "failed" if failed else ("quarantined" if quarantined else state)
    state_by_bead = dict(applied.get("association_state_by_bead") or {})
    if source and source not in state_by_bead:
        state_by_bead[source] = final_status
    counts = {
        "accepted": int(applied.get("accepted") or 0),
        "rejected": int(applied.get("rejected") or 0),
        "appended": int(applied.get("appended") or 0),
        "deduped": int(applied.get("deduped") or 0),
        "quarantined": quarantined,
        "failed": failed,
    }
    run_record = {
        "run_id": run_id_final,
        "status": "completed" if final_status in {"linked", "no_supported_links"} else final_status,
        "trigger": "operator",
        "policy_version": POLICY_VERSION,
        "prompt_version": prompt_v,
        "rubric_version": rubric_v,
        "graph_revision": _graph_revision(_load_index(root)),
        "session_id": _clean_str(session_id),
        "bead_ids": [source] if source else [],
        "association_state_by_bead": state_by_bead,
        "association_ids": list(applied.get("association_ids") or []),
        "candidate_count": 1,
        "counts": counts,
        "errors": list(applied.get("errors") or []),
        "contract": ASSOCIATION_CANDIDATE_DECISION_CONTRACT,
    }
    _append_run_record(root, run_record)
    _append_candidate_record(
        root,
        {
            "candidate_id": cid,
            "status": final_status,
            "run_id": run_id_final,
            "trigger": "operator",
            "graph_revision": run_record["graph_revision"],
            "source_bead": source,
            "target_bead": target,
            "proposed_relationship": rel,
            "decision": decision,
            "association_ids": list(applied.get("association_ids") or []),
        },
    )
    return {
        "ok": final_status in {"linked", "no_supported_links"},
        "contract": ASSOCIATION_CANDIDATE_DECISION_CONTRACT,
        "candidate_id": cid,
        "run_id": run_id_final,
        "status": final_status,
        "association_state_by_bead": state_by_bead,
        "association_ids": list(applied.get("association_ids") or []),
        "counts": counts,
        "errors": list(applied.get("errors") or []),
    }


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
    "ASSOCIATION_CANDIDATE_DECISION_CONTRACT",
    "ASSOCIATION_CANDIDATES_CONTRACT",
    "ASSOCIATION_COVERAGE_SUMMARY_CONTRACT",
    "ASSOCIATION_RUNS_SCHEMA",
    "POLICY_VERSION",
    "apply_association_proposals",
    "association_coverage_summary",
    "decide_association_candidate",
    "enqueue_association_coverage",
    "get_association_run",
    "latest_association_coverage",
    "list_association_candidates",
    "on_bead_committed",
    "run_association_coverage",
]
