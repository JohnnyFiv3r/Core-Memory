"""
Core-Memory store implementation.

This module contains the MemoryStore class which handles persistence.
Session-first live authority with index projection:
- session JSONL is the live append authority surface
- index.json is a fast projection/cache for retrieval convenience
- events provide audit trail and rebuild capability
"""

import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from ..schema.models import BeadType, Scope, Status, Authority
from ..persistence import events
from ..persistence.io_utils import store_lock, atomic_write_json, append_jsonl
from ..persistence.archive_index import append_archive_snapshot, read_snapshot, rebuild_archive_index
from ..runtime.session_surface import read_session_surface
from ..retrieval.query_norm import _tokenize, _is_memory_intent, _expand_query_tokens
from ..retrieval.lifecycle import mark_semantic_dirty, mark_trace_dirty
from ..retrieval.failure_patterns import compute_failure_signature, find_failure_signature_matches, preflight_failure_check
from ..policy.hygiene import enforce_bead_hygiene_contract
from ..policy.promotion import compute_promotion_score, compute_adaptive_threshold, is_candidate_promotable, get_recommendation_rows
from ..persistence.promotion_service import (
    promotion_slate_for_store,
    evaluate_candidates_for_store,
    decide_promotion_for_store,
    decide_promotion_bulk_for_store,
    decide_session_promotion_states_for_store,
    promotion_kpis_for_store,
    rebalance_promotions_for_store,
)

# Defaults for pip package (separate from live OpenClaw usage)
DEFAULT_ROOT = "."


class DiagnosticError(Exception):
    """Raised with recovery instructions when a persistence file is corrupt."""

    def __init__(self, message: str, recovery: str):
        self.recovery = recovery
        super().__init__(f"{message}\n  Recovery: {recovery}")
BEADS_DIR = ".beads"
TURNS_DIR = ".turns"
EVENTS_DIR = ".beads/events"
SESSION_FILE = "session-{id}.jsonl"
INDEX_FILE = "index.json"
HEADS_FILE = "heads.json"

# NOTE: durability model
# Archive/event writes happen under a store lock with fsync; index writes are atomic.
# We prefer archive-first for bead persistence so rebuild_index() can recover safely
# from archived JSONL + event logs.


class MemoryStore:
    """
    Persistent causal agent memory with lossless compaction.

    Live authority model:
    - session JSONL is authoritative for active-session writes/reads
    - index.json is maintained as projection/cache
    - events are append-only audit/rebuild logs

    Usage:
        memory = MemoryStore(root=".")
        memory.capture_turn(role="assistant", content="...")
        memory.consolidate(session_id="chat-123")
    """
    
    def __init__(self, root: str = DEFAULT_ROOT, backend: str = "json", tenant_id: str | None = None):
        """Initialize MemoryStore at the given root directory.

        Args:
            root: Root directory for memory storage.
            backend: Storage backend - "json" (default) or "sqlite".
                     Can also be set via CORE_MEMORY_BACKEND env var.
            tenant_id: Optional tenant ID for multi-tenant isolation.
                       Each tenant gets its own subtree under .beads/tenants/{tenant_id}/.
        """
        self.root = Path(root)
        self.tenant_id = tenant_id

        if tenant_id:
            self.beads_dir = self.root / BEADS_DIR / "tenants" / tenant_id
            self.turns_dir = self.root / TURNS_DIR / "tenants" / tenant_id
        else:
            self.beads_dir = self.root / BEADS_DIR
            self.turns_dir = self.root / TURNS_DIR
        self.metrics_state_file = self.beads_dir / "events" / "metrics-state.json"

        # Per-add association controls (fast derived links)
        self.associate_on_add = os.environ.get("CORE_MEMORY_ASSOCIATE_ON_ADD", "1") != "0"
        try:
            self.assoc_lookback = max(1, int(os.environ.get("CORE_MEMORY_ASSOCIATE_LOOKBACK", "40")))
        except ValueError:
            self.assoc_lookback = 40
        try:
            self.assoc_top_k = max(0, int(os.environ.get("CORE_MEMORY_ASSOCIATE_TOP_K", "3")))
        except ValueError:
            self.assoc_top_k = 3

        # Required-field rollout: warn-first by default; strict raises when enabled.
        self.strict_required_fields = os.environ.get("CORE_MEMORY_STRICT_REQUIRED_FIELDS", "0") == "1"
        # Bead schema invariant: session_id is required. Resolution modes:
        # - infer (default): infer from source_turn_ids; else fallback to "unknown"
        # - strict: require explicit/provable session_id
        # - unknown: always fallback to "unknown" when missing
        self.bead_session_id_mode = str(os.environ.get("CORE_MEMORY_BEAD_SESSION_ID_MODE", "infer") or "infer").strip().lower()
        # Agent-authoritative promotion: auto-promotion on compact is disabled by default.
        self.auto_promote_on_compact = os.environ.get("CORE_MEMORY_AUTO_PROMOTE_ON_COMPACT", "0") == "1"

        # Ensure directories exist
        self.beads_dir.mkdir(parents=True, exist_ok=True)
        self.turns_dir.mkdir(parents=True, exist_ok=True)

        # Storage backend (json default, sqlite opt-in)
        from ..persistence.backend import create_backend
        self._backend = create_backend(self.beads_dir, backend=backend)

        # Initialize index if needed
        self._init_index()

    def close(self) -> None:
        close_fn = getattr(self._backend, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception:
                pass

    def __del__(self):  # pragma: no cover
        try:
            self.close()
        except Exception:
            pass
    
    def _init_index(self):
        """Initialize the index + heads files if they don't exist."""
        index_file = self.beads_dir / INDEX_FILE
        heads_file = self.beads_dir / HEADS_FILE
        with store_lock(self.root):
            if not index_file.exists():
                self._write_json(index_file, {
                    "beads": {},
                    "associations": [],
                    "stats": {
                        "total_beads": 0,
                        "total_associations": 0,
                        "created_at": datetime.now(timezone.utc).isoformat()
                    },
                    "projection": {
                        "mode": "session_first_projection_cache",
                        "rebuilt_at": None,
                    },
                })
            if not heads_file.exists():
                self._write_json(heads_file, {
                    "topics": {},
                    "goals": {},
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
    
    def _read_json(self, path: Path) -> dict:
        """Read a JSON file. Raises DiagnosticError with recovery steps on corruption."""
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError as exc:
            raise DiagnosticError(
                f"Corrupt JSON file: {path} ({exc})",
                recovery=(
                    f"1. Back up the corrupt file: cp '{path}' '{path}.bak'\n"
                    f"  2. Rebuild from session authority: "
                    f"python -c \"from core_memory import MemoryStore; MemoryStore('{self.root}').rebuild_index_projection_from_sessions()\"\n"
                    f"  3. If rebuild fails, delete '{path}' and re-initialize."
                ),
            ) from exc
    
    def _write_json(self, path: Path, data: dict):
        """Write JSON atomically."""
        atomic_write_json(path, data)

    def rebuild_index_projection_from_sessions(self) -> dict:
        """Rebuild index projection from session/global JSONL surfaces.

        Authority model: session/global files are source; index is projection cache.
        Associations are preserved from existing index projection.
        """
        with store_lock(self.root):
            index_file = self.beads_dir / INDEX_FILE
            existing = self._read_json(index_file)
            associations = list(existing.get("associations") or [])

            beads = {}
            for p in sorted(self.beads_dir.glob("session-*.jsonl")):
                for row in read_session_surface(self.root, p.stem.replace("session-", "")):
                    bid = str((row or {}).get("id") or "")
                    if bid:
                        beads[bid] = row

            global_file = self.beads_dir / "global.jsonl"
            if global_file.exists():
                for line in global_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    bid = str((row or {}).get("id") or "")
                    if bid:
                        beads[bid] = row

            out = {
                "beads": beads,
                "associations": associations,
                "stats": {
                    "total_beads": len(beads),
                    "total_associations": len(associations),
                    "created_at": str((existing.get("stats") or {}).get("created_at") or datetime.now(timezone.utc).isoformat()),
                },
                "projection": {
                    "mode": "session_first_projection_cache",
                    "rebuilt_at": datetime.now(timezone.utc).isoformat(),
                },
            }
            self._write_json(index_file, out)
            return {
                "ok": True,
                "mode": "session_first_projection_cache",
                "total_beads": len(beads),
                "total_associations": len(associations),
            }
    
    def _generate_id(self) -> str:
        """Generate a short random bead ID (UUID-derived, non-ULID)."""
        return f"bead-{uuid.uuid4().hex[:12].upper()}"

    def _read_heads(self) -> dict:
        heads_file = self.beads_dir / HEADS_FILE
        if not heads_file.exists():
            return {"topics": {}, "goals": {}, "updated_at": datetime.now(timezone.utc).isoformat()}
        return self._read_json(heads_file)

    def _write_heads(self, heads: dict):
        heads["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_json(self.beads_dir / HEADS_FILE, heads)

    def _update_heads_for_bead(self, heads: dict, bead: dict) -> dict:
        topic_id = (bead.get("topic_id") or "").strip() if isinstance(bead.get("topic_id"), str) else ""
        goal_id = (bead.get("goal_id") or "").strip() if isinstance(bead.get("goal_id"), str) else ""
        bead_id = bead.get("id")
        if topic_id and bead_id:
            heads.setdefault("topics", {})[topic_id] = {
                "bead_id": bead_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        if goal_id and bead_id:
            heads.setdefault("goals", {})[goal_id] = {
                "bead_id": bead_id,
                "goal_status": bead.get("goal_status") or bead.get("status") or "open",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        return heads

    # LEGACY COMPATIBILITY - These methods delegate to extracted modules.
    # See retrieval/query_norm, retrieval/failure_patterns, hygiene, policy/promotion.

    def _tokenize(self, text: str) -> set[str]:
        from ..retrieval.query_norm import _tokenize as _tok
        return _tok(text)

    def _is_memory_intent(self, text: str) -> bool:
        from ..retrieval.query_norm import _is_memory_intent as _imi
        return _imi(text)

    def _expand_query_tokens(self, text: str, base_tokens: set[str], max_extra: int = 24) -> set[str]:
        from ..retrieval.query_norm import _expand_query_tokens as _eqt
        return _eqt(text, base_tokens, max_extra)

    def _redact_text(self, text: str) -> str:
        from ..policy.hygiene import _redact_text as _rt
        return _rt(text)

    def _sanitize_bead_content(self, bead: dict) -> dict:
        from ..policy.hygiene import sanitize_bead_content as _sbc
        return _sbc(bead)

    # Delegators to retrieval.failure_patterns
    def compute_failure_signature(self, plan: str) -> str:
        from ..retrieval.failure_patterns import compute_failure_signature as _cfs
        return _cfs(plan)

    def find_failure_signature_matches(
        self,
        plan: str = "",
        limit: int = 5,
        context_tags: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
    ) -> list[dict]:
        """Compatibility wrapper for failure-signature matching.

        Legacy callers may pass `tags=[...]` only; map that to a deterministic
        plan string and/or context_tags for ranking.
        """
        from ..retrieval.failure_patterns import find_failure_signature_matches as _fsm
        index = self._read_json(self.beads_dir / INDEX_FILE)

        tags_n = [str(t).strip().lower() for t in (tags or []) if str(t).strip()]
        plan_n = str(plan or "").strip()

        # Legacy ranking behavior: when only tags are provided, rank failed_hypothesis
        # by tag overlap first, then recency.
        if not plan_n and tags_n:
            req = set(tags_n)
            rows = []
            for b in (index.get("beads") or {}).values():
                if str(b.get("type") or "").strip().lower() != "failed_hypothesis":
                    continue
                bt = set(str(t).strip().lower() for t in (b.get("tags") or []) if str(t).strip())
                ov = len(req.intersection(bt))
                if ov <= 0:
                    continue
                row = dict(b)
                row["tag_overlap"] = ov
                rows.append(row)
            rows.sort(key=lambda r: (int(r.get("tag_overlap") or 0), str(r.get("created_at") or "")), reverse=True)
            return rows[: max(1, int(limit))]

        if not plan_n and tags_n:
            plan_n = " ".join(tags_n)
        ctx_n = context_tags if context_tags is not None else (tags_n or None)

        return _fsm(index, plan_n, limit=limit, context_tags=ctx_n)

    def preflight_failure_check(self, plan: str, limit: int = 5, context_tags: Optional[list[str]] = None) -> dict:
        from ..retrieval.failure_patterns import preflight_failure_check as _pfc
        index = self._read_json(self.beads_dir / INDEX_FILE)
        return _pfc(index, plan, limit=limit, context_tags=context_tags)

    # Delegator to hygiene.extract_constraints
    def extract_constraints(self, text: str) -> list[str]:
        from ..policy.hygiene import extract_constraints as _ec
        return _ec(text)

    def retrieve_with_context(
        self,
        *,
        query_text: str = "",
        context_tags: Optional[list[str]] = None,
        limit: int = 20,
        strict_first: bool = True,
        deep_recall: bool = False,
        max_uncompact_per_turn: int = 2,
        auto_memory_intent: bool = True,
    ) -> dict:
        """Context-aware retrieval with strict->fallback matching + bounded deep recall.

        Behavior:
        - strict pass: require overlap with requested context_tags
        - fallback pass: fill remaining slots by recency if strict underflows
        - deep recall (optional/heuristic): uncompact top compacted/archived hits when memory-intent detected
        """
        index = self._read_json(self.beads_dir / INDEX_FILE)
        beads = list(index.get("beads", {}).values())
        beads = [b for b in beads if str(b.get("status", "")).lower() != "superseded"]

        req_tags = [str(t).strip().lower() for t in (context_tags or []) if str(t).strip()]
        req_set = set(req_tags)
        query_tokens = self._expand_query_tokens(query_text, self._tokenize(query_text), max_extra=24)

        def score(bead: dict) -> tuple:
            bead_tags = set([str(t).strip().lower() for t in (bead.get("context_tags") or []) if str(t).strip()])
            tag_overlap = len(req_set.intersection(bead_tags)) if req_set else 0
            text_tokens = self._tokenize((bead.get("title") or "") + " " + " ".join(bead.get("summary") or []))
            text_overlap = len(query_tokens.intersection(text_tokens)) if query_tokens else 0
            ts = bead.get("promoted_at") or bead.get("created_at") or ""
            return (tag_overlap, text_overlap, ts)

        ranked = sorted(beads, key=score, reverse=True)

        strict = []
        fallback = []
        for b in ranked:
            bead_tags = set([str(t).strip().lower() for t in (b.get("context_tags") or []) if str(t).strip()])
            tag_overlap = len(req_set.intersection(bead_tags)) if req_set else 0
            row = {
                "id": b.get("id"),
                "type": b.get("type"),
                "title": b.get("title"),
                "summary": (b.get("summary") or [])[:2],
                "status": b.get("status"),
                "context_tags": b.get("context_tags") or [],
                "tag_overlap": tag_overlap,
                "created_at": b.get("created_at"),
                "detail_present": bool((b.get("detail") or "").strip()),
            }
            if req_set and tag_overlap > 0:
                strict.append(row)
            else:
                fallback.append(row)

        selected = []
        mode = "strict"
        if strict_first and req_set:
            selected.extend(strict[:limit])
            if len(selected) < limit:
                mode = "strict+fallback"
                selected.extend(fallback[: max(0, limit - len(selected))])
        else:
            mode = "fallback" if req_set else "global"
            selected = (strict + fallback)[:limit]

        should_deep_recall = bool(deep_recall or (auto_memory_intent and self._is_memory_intent(query_text)))
        uncompact_budget = max(0, int(max_uncompact_per_turn))
        uncompact_attempted = []
        uncompact_applied = []

        if should_deep_recall and uncompact_budget > 0:
            candidates = []
            for row in selected:
                status = str(row.get("status") or "").lower()
                if status in {"archived", "compacted"} and not row.get("detail_present"):
                    candidates.append(row)

            for row in candidates[:uncompact_budget]:
                bid = str(row.get("id") or "")
                if not bid:
                    continue
                uncompact_attempted.append(bid)
                res = self.uncompact(bid)
                if res.get("ok"):
                    uncompact_applied.append(bid)

            if uncompact_applied:
                # Refresh selected rows to expose newly-restored detail snippets.
                idx2 = self._read_json(self.beads_dir / INDEX_FILE)
                bead_map = idx2.get("beads", {})
                refreshed = []
                for row in selected:
                    bead = bead_map.get(str(row.get("id") or ""), {})
                    detail = (bead.get("detail") or "").strip()
                    row2 = dict(row)
                    row2["detail_present"] = bool(detail)
                    if detail:
                        row2["detail_preview"] = detail[:240]
                    refreshed.append(row2)
                selected = refreshed

        return {
            "ok": True,
            "mode": mode,
            "requested_context_tags": req_tags,
            "query_token_count": len(query_tokens),
            "strict_count": len(strict),
            "fallback_count": len(fallback),
            "deep_recall": {
                "enabled": should_deep_recall,
                "auto_memory_intent": bool(auto_memory_intent),
                "query_memory_intent": bool(self._is_memory_intent(query_text)),
                "max_uncompact_per_turn": uncompact_budget,
                "attempted": uncompact_attempted,
                "applied": uncompact_applied,
            },
            "results": selected[:limit],
        }

    def active_constraints(self, limit: int = 100) -> list[dict]:
        """Return active constraints from decision/design_principle/goal beads."""
        from ..persistence.store_constraints import active_constraints_for_store

        return active_constraints_for_store(self, limit=limit)

    def check_plan_constraints(self, plan: str, limit: int = 20) -> dict:
        """Advisory compliance check: map active constraints to satisfied/violated/unknown."""
        from ..persistence.store_constraints import check_plan_constraints_for_store

        return check_plan_constraints_for_store(self, plan=plan, limit=limit)

    def _read_metrics_state(self) -> dict:
        from ..reporting.store_metrics_runtime import read_metrics_state_for_store

        return read_metrics_state_for_store(self)

    def _write_metrics_state(self, state: dict):
        from ..reporting.store_metrics_runtime import write_metrics_state_for_store

        write_metrics_state_for_store(self, state)

    def start_task_run(self, run_id: str, task_id: str, mode: str = "core_memory", phase: str = "core_memory") -> dict:
        """Start/reset current metrics run context for step/tool aggregation."""
        from ..reporting.store_metrics_runtime import start_task_run_for_store

        return start_task_run_for_store(self, run_id, task_id, mode=mode, phase=phase)

    def track_step(self, count: int = 1) -> dict:
        from ..reporting.store_metrics_runtime import increment_metric_counter_for_store

        return increment_metric_counter_for_store(self, key="steps", count=count)

    def track_tool_call(self, count: int = 1) -> dict:
        from ..reporting.store_metrics_runtime import increment_metric_counter_for_store

        return increment_metric_counter_for_store(self, key="tool_calls", count=count)

    def track_turn_processed(self, count: int = 1) -> dict:
        from ..reporting.store_metrics_runtime import increment_metric_counter_for_store

        return increment_metric_counter_for_store(self, key="turns_processed", count=count)

    def track_bead_created(self, count: int = 1) -> dict:
        from ..reporting.store_metrics_runtime import increment_metric_counter_for_store

        return increment_metric_counter_for_store(self, key="beads_created", count=count)

    def track_bead_recalled(self, count: int = 1) -> dict:
        from ..reporting.store_metrics_runtime import increment_metric_counter_for_store

        return increment_metric_counter_for_store(self, key="beads_recalled", count=count)

    def current_run_metrics(self) -> dict:
        from ..reporting.store_metrics_runtime import current_run_metrics_for_store

        return current_run_metrics_for_store(self)

    def finalize_task_run(self, result: str = "success", **extra) -> dict:
        """Append final KPI row using current counters and derived compression ratio."""
        from ..reporting.store_metrics_runtime import finalize_task_run_for_store

        return finalize_task_run_for_store(self, result=result, **extra)

    def append_metric(self, record: dict) -> dict:
        """Append a metrics KPI record (v1 schema defaults applied)."""
        from ..reporting.store_metrics_runtime import append_metric_for_store

        return append_metric_for_store(self, record)

    def _infer_target_bead_for_question(self, question: str) -> Optional[dict]:
        """Infer target decision bead for a rationale question using token overlap."""
        from ..reporting.store_rationale import infer_target_bead_for_question

        return infer_target_bead_for_question(self, question)

    def evaluate_rationale_recall(self, question: str, answer: str, bead_id: Optional[str] = None) -> dict:
        """Deterministic 0/1/2 rationale recall scorer."""
        from ..reporting.store_rationale import evaluate_rationale_recall_for_store

        return evaluate_rationale_recall_for_store(self, question, answer, bead_id=bead_id)

    def metrics_report(self, since: str = "7d") -> dict:
        """Deterministic metrics aggregation delegated to reporting module."""
        from ..reporting.store_reporting import metrics_report_for_store

        return metrics_report_for_store(self, since=since)

    def append_autonomy_kpi(
        self,
        *,
        run_id: str,
        repeat_failure: bool = False,
        contradiction_resolved: bool = False,
        contradiction_latency_turns: int = 0,
        unjustified_flip: bool = False,
        constraint_violation: bool = False,
        wrong_transfer: bool = False,
        goal_carryover: bool = False,
    ) -> dict:
        """Append one autonomy KPI row (Phase 5 proof loop)."""
        rec = {
            "run_id": run_id,
            "mode": "core_memory",
            "task_id": "autonomy_kpi",
            "result": "success",
            "steps": 0,
            "tool_calls": 0,
            "beads_created": 0,
            "beads_recalled": 0,
            "repeat_failure": bool(repeat_failure),
            "decision_conflicts": 1 if contradiction_resolved else 0,
            "unjustified_flips": 1 if unjustified_flip else 0,
            "rationale_recall_score": 0,
            "turns_processed": 1,
            "compression_ratio": 0.0,
            "phase": "autonomy",
            "kpi_contradiction_resolved": bool(contradiction_resolved),
            "kpi_contradiction_latency_turns": max(0, int(contradiction_latency_turns)),
            "kpi_constraint_violation": bool(constraint_violation),
            "kpi_wrong_transfer": bool(wrong_transfer),
            "kpi_goal_carryover": bool(goal_carryover),
        }
        return self.append_metric(rec)

    def autonomy_report(self, since: str = "7d") -> dict:
        """Aggregate autonomy KPIs delegated to reporting module."""
        from ..reporting.store_reporting import autonomy_report_for_store

        return autonomy_report_for_store(self, since=since)

    def schema_quality_report(self, write_path: Optional[str] = None) -> dict:
        """Report required-field warnings and promotion gate blockers."""
        from ..reporting.store_reporting import schema_quality_report_for_store

        return schema_quality_report_for_store(self, write_path=write_path)

    def _reinforcement_signals(self, index: dict, bead: dict) -> dict:
        bead_id = str(bead.get("id") or "")
        if not bead_id:
            return {"count": 0}

        bead_links = self._normalize_links(bead.get("links"))
        links_in = 0
        links_out = len(bead_links)
        for other in (index.get("beads") or {}).values():
            if other.get("id") == bead_id:
                continue
            if str(other.get("linked_bead_id") or "") == bead_id:
                links_in += 1
                continue
            for l in self._normalize_links(other.get("links")):
                if str((l or {}).get("bead_id") or "") == bead_id:
                    links_in += 1
                    break

        assoc_deg = 0
        for a in (index.get("associations") or []):
            if not (a.get("source_bead") == bead_id or a.get("target_bead") == bead_id):
                continue
            edge_class = str(a.get("edge_class") or "").lower()
            rel = str(a.get("relationship") or "").lower()
            # Count only stronger/non-derived reinforcement signals.
            if edge_class == "derived" and rel in {"shared_tag", "follows", "related"}:
                continue
            assoc_deg += 1

        recurrence = len(bead.get("source_turn_ids") or []) >= 2
        recalled = int(bead.get("recall_count") or 0) > 0

        cnt = 0
        for v in [links_in > 0 or links_out > 0, assoc_deg > 0, recurrence, recalled]:
            cnt += 1 if v else 0

        return {
            "links_in": links_in,
            "links_out": links_out,
            "association_degree": assoc_deg,
            "recurrence": recurrence,
            "recalled": recalled,
            "count": cnt,
        }

    def _promotion_score(self, index: dict, bead: dict) -> tuple[float, dict]:
        return compute_promotion_score(index, bead)

    def _adaptive_promotion_threshold(self, index: dict) -> float:
        return compute_adaptive_threshold(index)

    def _candidate_promotable(self, index: dict, bead: dict) -> tuple[bool, dict]:
        return is_candidate_promotable(index, bead)

    def _candidate_recommendation_rows(self, index: dict, query_text: str = "") -> tuple[list[dict], float]:
        return get_recommendation_rows(
            index,
            query_text=query_text,
            query_tokenize_fn=self._tokenize,
            query_expand_fn=self._expand_query_tokens,
        )

    def promotion_slate(self, limit: int = 20, query_text: str = "") -> dict:
        """Build bounded candidate promotion slate with advisory recommendations."""
        return promotion_slate_for_store(self, limit=limit, query_text=query_text)

    def evaluate_candidates(
        self,
        limit: int = 50,
        query_text: str = "",
        auto_archive_hold: bool = False,
        min_age_hours: int = 12,
    ) -> dict:
        """Refresh advisory recommendation fields for candidates."""
        return evaluate_candidates_for_store(
            self,
            limit=limit,
            query_text=query_text,
            auto_archive_hold=auto_archive_hold,
            min_age_hours=min_age_hours,
        )

    def decide_promotion(
        self,
        *,
        bead_id: str,
        decision: str,
        reason: str = "",
        considerations: Optional[list[str]] = None,
    ) -> dict:
        """Apply agent-led promotion decision for a bead."""
        return decide_promotion_for_store(
            self,
            bead_id=bead_id,
            decision=decision,
            reason=reason,
            considerations=considerations,
        )

    def decide_promotion_bulk(self, decisions: list[dict]) -> dict:
        """Apply a bounded batch of agent promotion decisions."""
        return decide_promotion_bulk_for_store(self, decisions)

    def decide_session_promotion_states(self, *, session_id: str, visible_bead_ids: Optional[list[str]] = None, turn_id: str = "") -> dict:
        """Per-turn session decision pass: promoted|candidate|null for visible beads."""
        return decide_session_promotion_states_for_store(
            self,
            session_id=session_id,
            visible_bead_ids=visible_bead_ids,
            turn_id=turn_id,
        )

    def promotion_kpis(self, limit: int = 500) -> dict:
        """Report promotion decision volume, reasons, and rec-vs-decision alignment."""
        return promotion_kpis_for_store(self, limit=limit)

    def rebalance_promotions(self, apply: bool = False) -> dict:
        """Phase B: score promoted beads and demote weakly-supported promotions."""
        return rebalance_promotions_for_store(self, apply=apply)

    def _normalize_links(self, links) -> list[dict]:
        from ..persistence.store_validation_helpers import normalize_links

        return normalize_links(links)

    def _has_evidence(self, bead: dict) -> bool:
        from ..persistence.store_validation_helpers import has_evidence

        return has_evidence(bead)

    def _required_field_issues(self, bead: dict) -> list[str]:
        from ..persistence.store_validation_helpers import required_field_issues_for_store

        return required_field_issues_for_store(self, bead)

    def _validate_bead_fields(self, bead: dict):
        from ..persistence.store_validation_helpers import validate_bead_fields_for_store

        validate_bead_fields_for_store(self, bead)

    def _resolve_bead_session_id(self, *, session_id: Optional[str], source_turn_ids: Optional[list]) -> str:
        from ..persistence.store_add_helpers import resolve_bead_session_id_for_store

        return resolve_bead_session_id_for_store(self, session_id=session_id, source_turn_ids=source_turn_ids)

    def _title_tokens(self, text: str) -> set[str]:
        from ..persistence.store_add_helpers import title_tokens_for_store

        return title_tokens_for_store(self, text)

    def _is_contradictory_decision(self, a_title: str, b_title: str) -> bool:
        from ..persistence.store_add_helpers import is_contradictory_decision

        return is_contradictory_decision(a_title, b_title)

    def _detect_decision_conflicts(self, index: dict, bead: dict) -> tuple[int, int, list[str]]:
        from ..persistence.store_add_helpers import detect_decision_conflicts_for_store

        return detect_decision_conflicts_for_store(self, index, bead)

    def _quick_association_candidates(self, index: dict, bead: dict, max_lookback: int = 40, top_k: int = 3) -> list[dict]:
        """Fast, deterministic association inference for newly added beads."""
        from ..association import run_association_pass

        return run_association_pass(index, bead, max_lookback=max_lookback, top_k=top_k)

    def _norm_text(self, s: str) -> str:
        from ..persistence.store_add_helpers import norm_text

        return norm_text(s)

    def _bead_similarity(self, a: dict, b: dict) -> float:
        from ..persistence.store_add_helpers import bead_similarity

        return bead_similarity(a, b)

    def _find_recent_duplicate_bead_id(self, index: dict, bead: dict, session_id: str | None, window: int = 25) -> str | None:
        from ..persistence.store_add_helpers import find_recent_duplicate_bead_id_for_store

        return find_recent_duplicate_bead_id_for_store(
            self,
            index,
            bead,
            session_id=session_id,
            window=window,
        )
    
    # === Core API ===
    
    def add_bead(
        self,
        type: str,
        title: str,
        summary: Optional[list] = None,
        because: Optional[list] = None,
        source_turn_ids: Optional[list] = None,
        detail: str = "",
        session_id: Optional[str] = None,
        scope: str = "project",
        tags: Optional[list] = None,
        links: Optional[dict] = None,
        **kwargs
    ) -> str:
        """
        Create a new bead.
        
        Args:
            type: Bead type (BeadType enum or string)
            title: Short descriptive title
            summary: List of key points
            detail: Full narrative (preserved in archive)
            session_id: Associated session
            scope: Scope (Scope enum or string)
            tags: List of tags
            links: Causal/associative links
            
        Returns:
            Bead ID
        """
        from ..schema.models import BeadType, Scope
        
        # Normalize enums to strings
        type_value = self._normalize_enum(type, BeadType)
        scope_value = self._normalize_enum(scope, Scope)

        # P7 confirmed decision: association is not a bead type.
        if type_value == "association":
            raise ValueError("association_is_not_a_bead_type")

        reserved_overrides = {"id"}
        bad_override = sorted(set(str(k) for k in kwargs.keys()).intersection(reserved_overrides))
        if bad_override:
            raise ValueError(f"reserved_overrides_not_allowed:{','.join(bad_override)}")

        bead_id = self._generate_id()
        now = datetime.now(timezone.utc).isoformat()
        
        resolved_session_id = self._resolve_bead_session_id(session_id=session_id, source_turn_ids=source_turn_ids)

        bead = {
            "id": bead_id,
            "type": type_value,
            "created_at": now,
            "session_id": resolved_session_id,
            "title": title,
            "summary": summary or [],
            "because": because or [],
            "source_turn_ids": source_turn_ids or [],
            "detail": detail,
            "scope": scope_value,
            "authority": "agent_inferred",
            "confidence": 0.8,
            "tags": tags or [],
            "links": self._normalize_links(links),
            "status": "open",
            "recall_count": 0,
            "last_recalled": None,
            **kwargs
        }

        # conservative secret redaction (high-confidence patterns only)
        bead = self._sanitize_bead_content(bead)

        # Thin-vs-rich hygiene normalization:
        # - keeps one-bead-per-turn invariant
        # - preserves temporal minimum surface
        # - payload-gates retrieval eligibility
        bead = enforce_bead_hygiene_contract(bead)

        # Phase 3 advisory constraint extraction for commitments/principles
        if bead.get("type") in {"decision", "design_principle", "goal"} and not bead.get("constraints"):
            basis = " ".join([bead.get("title", "")] + list(bead.get("summary") or []))
            extracted = self.extract_constraints(basis)
            if extracted:
                bead["constraints"] = extracted

        # stable failure signature for FAILED_HYPOTHESIS beads
        if bead.get("type") == "failed_hypothesis":
            basis = " ".join(bead.get("summary", [])) or bead.get("title", "") or bead.get("detail", "")
            bead["failure_signature"] = self.compute_failure_signature(basis)

        self._validate_bead_fields(bead)

        repeat_failure = False
        decision_conflicts = 0
        unjustified_flips = 0

        with store_lock(self.root):
            index_file = self.beads_dir / INDEX_FILE
            index = self._read_json(index_file)

            # Write-time duplicate suppression (same-session, recent window).
            # Keeps corpus signal dense and avoids duplicate-shape beads.
            try:
                dedup_window = max(1, int(os.environ.get("CORE_MEMORY_WRITE_DEDUP_WINDOW", "25")))
            except ValueError:
                dedup_window = 25
            dup_id = self._find_recent_duplicate_bead_id(index, bead, session_id=resolved_session_id, window=dedup_window)
            if dup_id:
                return dup_id

            # Write to session archive first (durability/rebuild source)
            if resolved_session_id:
                bead_file = self.beads_dir / SESSION_FILE.format(id=resolved_session_id)
            else:
                bead_file = self.beads_dir / "global.jsonl"
            append_jsonl(bead_file, bead)

            # Update index after durable archive write

            if bead.get("type") == "failed_hypothesis" and bead.get("failure_signature"):
                sig = bead.get("failure_signature")
                repeat_failure = any(
                    b.get("failure_signature") == sig
                    for b in index.get("beads", {}).values()
                )

            decision_conflicts, unjustified_flips, conflict_ids = self._detect_decision_conflicts(index, bead)
            if conflict_ids:
                bead["decision_conflict_with"] = conflict_ids
                bead["unjustified_flip"] = bool(unjustified_flips)

            index["beads"][bead["id"]] = bead
            index["stats"]["total_beads"] = len(index["beads"])

            # Fast per-add association pass (derived, deterministic, bounded)
            # Session-first authority: build pass input from session surface first,
            # then fall back to index projection for missing ids.
            candidates = []
            if self.associate_on_add and self.assoc_top_k > 0:
                assoc_index = dict(index)
                assoc_beads = dict(index.get("beads") or {})
                if resolved_session_id:
                    for row in read_session_surface(self.root, resolved_session_id):
                        rid = str((row or {}).get("id") or "")
                        if rid:
                            assoc_beads[rid] = row
                assoc_index["beads"] = assoc_beads
                candidates = self._quick_association_candidates(
                    assoc_index,
                    bead,
                    max_lookback=self.assoc_lookback,
                    top_k=self.assoc_top_k,
                )

            bead["association_preview"] = [
                {
                    "bead_id": c["other_id"],
                    "relationship": c["relationship"],
                    "score": c["score"],
                    "authoritative": False,
                    "source": "store_quick_preview",
                }
                for c in candidates
            ]
            index["beads"][bead["id"]] = bead

            # V2P13 Step 1: store-level quick association pass is preview-only.
            # Canonical association authorship is owned by crawler-reviewed updates
            # and flush-merge projection paths.
            index["associations"] = sorted(
                index.get("associations", []),
                key=lambda a: (a.get("created_at", ""), a.get("id", "")),
            )
            index["stats"]["total_associations"] = len(index.get("associations", []))
            index["projection"] = {
                "mode": "session_first_projection_cache",
                "rebuilt_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write_json(index_file, index)

            # Update canonical HEAD pointers (topic/goal identity)
            heads = self._read_heads()
            heads = self._update_heads_for_bead(heads, bead)
            self._write_heads(heads)

            # Append audit event (minimal - just id + timestamp for rebuild)
            events.event_bead_created(self.root, resolved_session_id, bead_id, now, use_lock=False)

            # Append metrics event (append-only, no index mutation)
            events.append_metric(self.root, {
                "ts": now,
                "run_id": f"bead-{bead_id}",
                "mode": "core_memory",
                "task_id": bead.get("type", "unknown"),
                "result": "success",
                "steps": 1,
                "tool_calls": 0,
                "beads_created": 1,
                "beads_recalled": 0,
                "repeat_failure": repeat_failure,
                "decision_conflicts": decision_conflicts,
                "unjustified_flips": unjustified_flips,
                "rationale_recall_score": 0,
                "turns_processed": 1,
                "compression_ratio": 1.0,
                "phase": "core_memory",
            }, use_lock=False)

        # aggregate run counters (outside lock helper has its own lock)
        self.track_bead_created(1)

        # canonical retrieval lifecycle: bead mutation marks semantic corpus dirty
        mark_semantic_dirty(self.root, reason="add_bead")

        return bead_id
    
    def capture_turn(
        self,
        role: str,
        content: str,
        tools_used: Optional[list] = None,
        user_message: str = "",
        session_id: str = "default"
    ):
        """Capture a single turn in the session."""
        from ..persistence.store_session_ops import capture_turn_for_store

        capture_turn_for_store(
            self,
            role=role,
            content=content,
            tools_used=tools_used,
            user_message=user_message,
            session_id=session_id,
        )
    
    def consolidate(self, session_id: str = "default") -> dict:
        """Run session-end consolidation summary bead."""
        from ..persistence.store_session_ops import consolidate_for_store

        return consolidate_for_store(self, session_id=session_id)

    def compact(
        self,
        session_id: Optional[str] = None,
        promote: bool = False,
        only_bead_ids: Optional[list[str]] = None,
        skip_bead_ids: Optional[list[str]] = None,
        force_archive_all: bool = False,
    ) -> dict:
        """Core-native compact: archive detail text losslessly and optionally promote.

        - only_bead_ids: if provided, compact only this explicit set
        - skip_bead_ids: if provided, skip compacting these IDs
        """
        with store_lock(self.root):
            index = self._read_json(self.beads_dir / INDEX_FILE)
            compacted = 0
            only = set(only_bead_ids or [])
            skip = set(skip_bead_ids or [])

            for bead_id in sorted(index.get("beads", {}).keys()):
                bead = index["beads"][bead_id]
                if session_id and bead.get("session_id") != session_id:
                    continue
                if only and bead_id not in only:
                    continue
                if bead_id in skip:
                    continue

                # Optional auto-promote pass (disabled by default; agent-led decisions preferred).
                if promote and self.auto_promote_on_compact and bead.get("status") != "promoted":
                    btype = str(bead.get("type") or "").lower()
                    curr_status = str(bead.get("status") or "").lower()
                    because = bead.get("because") or []
                    has_evidence = self._has_evidence(bead)
                    detail_now = (bead.get("detail") or "").strip()
                    has_link = bool(str(bead.get("linked_bead_id") or "").strip()) or bool(bead.get("links"))
                    allow_promote = False
                    score_meta = None
                    if curr_status == "candidate":
                        # Keep minimum quality pre-check per type.
                        quality_gate = False
                        if btype == "decision":
                            quality_gate = bool(because and (has_evidence or detail_now or has_link))
                        elif btype == "lesson":
                            quality_gate = bool(because and (has_evidence or detail_now or has_link))
                        elif btype == "outcome":
                            result = str(bead.get("result") or "").strip().lower()
                            quality_gate = result in {"resolved", "failed", "partial", "confirmed"} and (has_link or has_evidence or detail_now)
                        elif btype == "precedent":
                            quality_gate = bool(str(bead.get("condition") or "").strip() and str(bead.get("action") or "").strip())
                        elif btype in {"evidence", "design_principle", "failed_hypothesis"}:
                            quality_gate = bool(has_evidence or detail_now or has_link)

                        if quality_gate:
                            allow_promote, score_meta = self._candidate_promotable(index, bead)

                    if allow_promote:
                        bead["status"] = "promoted"
                        bead["promotion_state"] = "promoted"
                        bead["promotion_locked"] = True
                        bead["promoted_at"] = datetime.now(timezone.utc).isoformat()
                        if score_meta:
                            bead["promotion_score"] = score_meta.get("score")
                            bead["promotion_threshold"] = score_meta.get("threshold")
                            bead["promotion_reason"] = str(bead.get("promotion_reason") or f"{score_meta.get('reason')}:{score_meta.get('score')}")
                        else:
                            bead["promotion_reason"] = str(bead.get("promotion_reason") or "policy_auto_promote")

                # Invariants:
                # - promoted beads always keep full detail
                # - session boundary beads always keep full detail
                bead_type = str(bead.get("type", "")).lower()
                bead_status = str(bead.get("status", "")).lower()
                is_session_boundary = bead_type in {"session_start", "session_end"}
                is_promoted = bead_status == "promoted"

                # Default behavior keeps candidates active for reinforcement window (Phase B).
                # On session_flush authority path, callers can force archival of all eligible beads.
                if (not force_archive_all) and bead_status == "candidate":
                    index["beads"][bead_id] = bead
                    continue

                should_archive = force_archive_all or (not is_promoted and not is_session_boundary)
                if should_archive:
                    already_archived = str(bead.get("status") or "").lower() == "archived"
                    has_ptr = isinstance(bead.get("archive_ptr"), dict) and bool((bead.get("archive_ptr") or {}).get("revision_id"))
                    has_detail = bool((bead.get("detail") or "").strip())
                    if not (already_archived and has_ptr and not has_detail):
                        # Archive full pre-compaction snapshot as append-only revision.
                        revision_id = f"rev-{uuid.uuid4().hex[:12]}"
                        archive = {
                            "bead_id": bead_id,
                            "revision_id": revision_id,
                            "archived_at": datetime.now(timezone.utc).isoformat(),
                            "archived_from_status": bead.get("status"),
                            "snapshot": dict(bead),
                        }
                        append_archive_snapshot(self.root, archive)
                        bead["archive_ptr"] = {"revision_id": revision_id}

                        # Compact into skeleton representation.
                        bead["detail"] = ""
                        bead["summary"] = (bead.get("summary") or [])[:2]
                        bead["status"] = "archived"
                        compacted += 1

                index["beads"][bead_id] = bead

            self._write_json(self.beads_dir / INDEX_FILE, index)
            mark_semantic_dirty(self.root, reason="compact")
            return {
                "ok": True,
                "compacted": compacted,
                "session": session_id,
                "only_bead_ids": len(only),
                "skip_bead_ids": len(skip),
                "force_archive_all": bool(force_archive_all),
            }

    def uncompact(self, bead_id: str) -> dict:
        """Restore compacted bead detail from append-only archive revisions."""
        with store_lock(self.root):
            index = self._read_json(self.beads_dir / INDEX_FILE)
            if bead_id not in index.get("beads", {}):
                return {"ok": False, "error": f"Bead not found: {bead_id}"}

            bead = index["beads"][bead_id]
            wanted_rev = ((bead.get("archive_ptr") or {}).get("revision_id") if isinstance(bead.get("archive_ptr"), dict) else None)

            found = read_snapshot(self.root, str(wanted_rev or "")) if wanted_rev else None

            if not found:
                # Fallback for legacy rows / missing index: linear scan then optionally rebuild index.
                archive_file = self.beads_dir / "archive.jsonl"
                if not archive_file.exists():
                    return {"ok": False, "error": f"Bead not found in archive: {bead_id}"}
                with open(archive_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        row = json.loads(line)
                        if row.get("bead_id") != bead_id:
                            continue
                        if wanted_rev and row.get("revision_id") != wanted_rev:
                            continue
                        found = row

                if wanted_rev and not read_snapshot(self.root, str(wanted_rev or "")):
                    rebuild_archive_index(self.root)

            if not found:
                return {"ok": False, "error": f"Bead not found in archive: {bead_id}"}

            # New format: full snapshot. Legacy fallback: detail/summary fields.
            snapshot = found.get("snapshot") if isinstance(found.get("snapshot"), dict) else None
            if snapshot:
                restored = dict(snapshot)
                restored["status"] = "open" if bead.get("status") == "archived" else bead.get("status")
                restored["uncompacted_at"] = datetime.now(timezone.utc).isoformat()
                index["beads"][bead_id] = restored
            else:
                bead["detail"] = found.get("detail", "")
                if found.get("summary"):
                    bead["summary"] = found.get("summary")
                if bead.get("status") == "archived":
                    bead["status"] = "open"
                bead["uncompacted_at"] = datetime.now(timezone.utc).isoformat()
                index["beads"][bead_id] = bead

            self._write_json(self.beads_dir / INDEX_FILE, index)
            mark_semantic_dirty(self.root, reason="uncompact")
            return {"ok": True, "id": bead_id, "revision_id": found.get("revision_id")}

    def myelinate(self, apply: bool = False) -> dict:
        """Core-native myelination scaffold (deterministic)."""
        index = self._read_json(self.beads_dir / INDEX_FILE)
        actions = []
        # Deterministic scan, no destructive behavior until policy finalization.
        for bead_id in sorted(index.get("beads", {}).keys()):
            bead = index["beads"][bead_id]
            if bead.get("recall_count", 0) >= 3:
                actions.append({"bead_id": bead_id, "action": "retain"})

        return {
            "dry_run": not apply,
            "total_derived_edges": 0,
            "edges_with_actions": len(actions),
            "actions": actions[:50],
        }

    def _normalize_enum(self, value, enum_class):
        """Normalize enum or string to string value."""
        if value is None:
            return None
        if isinstance(value, enum_class):
            return value.value
        return str(value)
    
    def query(
        self,
        type: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[list] = None,
        scope: Optional[str] = None,
        limit: int = 20,
        session_id: Optional[str] = None,
    ) -> list:
        """Query beads with filters."""
        from ..persistence.store_query import query_for_store

        return query_for_store(
            self,
            type=type,
            status=status,
            tags=tags,
            scope=scope,
            limit=limit,
            session_id=session_id,
        )
    
    def promote(self, bead_id: str, promotion_reason: Optional[str] = None) -> bool:
        """
        Promote a bead to long-term memory.

        High-value types enforce stricter promotion quality gates.
        """
        with store_lock(self.root):
            index = self._read_json(self.beads_dir / INDEX_FILE)

            if bead_id not in index["beads"]:
                return False

            bead = index["beads"][bead_id]
            btype = str(bead.get("type") or "").lower()
            because = bead.get("because") or []
            detail = (bead.get("detail") or "").strip()
            has_evidence = self._has_evidence(bead)

            # Strict promotion gates for high-value beads
            if btype in {"decision", "lesson", "outcome", "precedent"}:
                if btype == "decision" and not (because and (has_evidence or detail)):
                    return False
                if btype == "lesson" and not because:
                    return False
                if btype == "outcome":
                    result = str(bead.get("result") or "").strip().lower()
                    has_link = bool(str(bead.get("linked_bead_id") or "").strip()) or bool(bead.get("links"))
                    if result not in {"resolved", "failed", "partial", "confirmed"}:
                        return False
                    if not (has_link or has_evidence):
                        return False
                if btype == "precedent":
                    if not (str(bead.get("condition") or "").strip() and str(bead.get("action") or "").strip()):
                        return False

            bead["status"] = "promoted"
            bead["promotion_state"] = "promoted"
            bead["promotion_locked"] = True
            bead["promoted_at"] = datetime.now(timezone.utc).isoformat()
            bead["promotion_reason"] = (promotion_reason or bead.get("promotion_reason") or "policy_auto_promote").strip()

            index["beads"][bead_id] = bead
            self._write_json(self.beads_dir / INDEX_FILE, index)

            # Append audit event (rebuild support)
            events.event_bead_promoted(self.root, bead_id, use_lock=False)

            mark_semantic_dirty(self.root, reason="promote")

            return True
    
    def link(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        explanation: str = "",
        confidence: float = 0.8,
    ) -> str:
        """
        Create a link between two beads.
        
        Args:
            source_id: Source bead ID
            target_id: Target bead ID
            relationship: Link type (caused_by, led_to, contradicts, etc.)
            explanation: Why they're linked
            
        Returns:
            Association ID
        """
        assoc_id = f"assoc-{uuid.uuid4().hex[:12].upper()}"
        
        assoc = {
            "id": assoc_id,
            "type": "association",
            "source_bead": source_id,
            "target_bead": target_id,
            "relationship": relationship,
            "explanation": explanation,
            "confidence": float(confidence),
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        with store_lock(self.root):
            index = self._read_json(self.beads_dir / INDEX_FILE)
            index["associations"].append(assoc)
            index["stats"]["total_associations"] += 1
            self._write_json(self.beads_dir / INDEX_FILE, index)

            # Append audit event (rebuild support)
            events.event_association_created(self.root, assoc, use_lock=False)

            mark_trace_dirty(self.root, reason="link")

            return assoc_id
    
    def recall(self, bead_id: str) -> bool:
        """
        Record a recall (strengthens association, myelination).
        
        Args:
            bead_id: ID of bead being recalled
            
        Returns:
            Success
        """
        with store_lock(self.root):
            index = self._read_json(self.beads_dir / INDEX_FILE)

            if bead_id not in index["beads"]:
                return False

            bead = index["beads"][bead_id]
            bead["recall_count"] = bead.get("recall_count", 0) + 1
            bead["last_recalled"] = datetime.now(timezone.utc).isoformat()

            index["beads"][bead_id] = bead
            self._write_json(self.beads_dir / INDEX_FILE, index)

            # Append audit event (rebuild support)
            events.event_bead_recalled(self.root, bead_id, use_lock=False)

            # Edge traversal telemetry for myelination/reinforcement modeling
            for assoc in index.get("associations", []):
                if assoc.get("source_bead") == bead_id or assoc.get("target_bead") == bead_id:
                    events.event_edge_traversed(
                        self.root,
                        edge_id=assoc.get("id", ""),
                        source_bead=assoc.get("source_bead"),
                        target_bead=assoc.get("target_bead"),
                        use_lock=False,
                    )

        self.track_bead_recalled(1)
        return True
    
    def dream(self, novel_only: bool = False, seen_window_runs: int = 0, max_exposure: int = -1) -> list:
        """
        Run Dreamer association analysis.

        Args:
            novel_only: Exclude previously surfaced bead pairs
            seen_window_runs: Use only last N runs when deduping seen pairs (0=all)
            max_exposure: Skip candidates when either bead has been surfaced more than this count (-1=disabled)

        Returns:
            List of discovered associations
        """
        try:
            from .. import dreamer
            # Pass the store instance for decoupled access
            return dreamer.run_analysis(
                store=self,
                novel_only=novel_only,
                seen_window_runs=seen_window_runs,
                max_exposure=max_exposure,
            )
        except ImportError:
            return [{"error": "Dreamer not available"}]
    
    def rebuild_index(self) -> dict:
        """
        Rebuild the index from all events.
        
        This is the canonical way to ensure index consistency.
        Call this if you suspect index corruption.
        
        Returns:
            The rebuilt index
        """

        return events.rebuild_index(self.root)
    
    def stats(self) -> dict:
        """Get memory statistics."""
        index = self._read_json(self.beads_dir / INDEX_FILE)
        
        by_type = {}
        by_status = {}
        for bead in index.get("beads", {}).values():
            t = bead.get("type", "unknown")
            s = bead.get("status", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
            by_status[s] = by_status.get(s, 0) + 1
        
        return {
            "total_beads": len(index.get("beads", {})),
            "total_associations": len(index.get("associations", [])),
            "by_type": by_type,
            "by_status": by_status
        }
    
    # === Internal ===
    
    def _update_index(self, bead: dict):
        """Update the index with a new/updated bead."""
        index_file = self.beads_dir / INDEX_FILE
        index = self._read_json(index_file)
        
        index["beads"][bead["id"]] = bead
        index["stats"]["total_beads"] = len(index["beads"])
        
        self._write_json(index_file, index)
