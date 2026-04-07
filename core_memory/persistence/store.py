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
from ..persistence.io_utils import store_lock, atomic_write_json
from ..retrieval.query_norm import _tokenize, _is_memory_intent, _expand_query_tokens
from ..retrieval.failure_patterns import compute_failure_signature, find_failure_signature_matches, preflight_failure_check
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
        """Rebuild index projection from session/global JSONL surfaces."""
        from ..persistence.store_projection_ops import rebuild_index_projection_from_sessions_for_store

        return rebuild_index_projection_from_sessions_for_store(self)
    
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
        """Context-aware retrieval with strict->fallback matching + bounded deep recall."""
        from ..persistence.store_retrieval_context import retrieve_with_context_for_store

        return retrieve_with_context_for_store(
            self,
            query_text=query_text,
            context_tags=context_tags,
            limit=limit,
            strict_first=strict_first,
            deep_recall=deep_recall,
            max_uncompact_per_turn=max_uncompact_per_turn,
            auto_memory_intent=auto_memory_intent,
        )

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
        """Create a new bead."""
        from ..persistence.store_add_bead_ops import add_bead_for_store

        return add_bead_for_store(
            self,
            type=type,
            title=title,
            summary=summary,
            because=because,
            source_turn_ids=source_turn_ids,
            detail=detail,
            session_id=session_id,
            scope=scope,
            tags=tags,
            links=links,
            **kwargs,
        )
    
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
        """Core-native compact: archive detail text losslessly and optionally promote."""
        from ..persistence.store_compaction_ops import compact_for_store

        return compact_for_store(
            self,
            session_id=session_id,
            promote=promote,
            only_bead_ids=only_bead_ids,
            skip_bead_ids=skip_bead_ids,
            force_archive_all=force_archive_all,
        )

    def uncompact(self, bead_id: str) -> dict:
        """Restore compacted bead detail from append-only archive revisions."""
        from ..persistence.store_compaction_ops import uncompact_for_store

        return uncompact_for_store(self, bead_id=bead_id)

    def myelinate(self, apply: bool = False) -> dict:
        """Core-native myelination scaffold (deterministic)."""
        from ..persistence.store_compaction_ops import myelinate_for_store

        return myelinate_for_store(self, apply=apply)

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
        """Promote a bead to long-term memory."""
        from ..persistence.store_relationship_ops import promote_for_store

        return promote_for_store(self, bead_id=bead_id, promotion_reason=promotion_reason)
    
    def link(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        explanation: str = "",
        confidence: float = 0.8,
    ) -> str:
        """Create a link between two beads."""
        from ..persistence.store_relationship_ops import link_for_store

        return link_for_store(
            self,
            source_id=source_id,
            target_id=target_id,
            relationship=relationship,
            explanation=explanation,
            confidence=confidence,
        )
    
    def recall(self, bead_id: str) -> bool:
        """Record a recall (strengthens association, myelination)."""
        from ..persistence.store_relationship_ops import recall_for_store

        return recall_for_store(self, bead_id=bead_id)
    
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
        """Rebuild the index from all events."""
        from ..persistence.store_relationship_ops import rebuild_index_for_store

        return rebuild_index_for_store(self)
    
    def stats(self) -> dict:
        """Get memory statistics."""
        from ..persistence.store_relationship_ops import stats_for_store

        return stats_for_store(self)
    
    # === Internal ===
    
    def _update_index(self, bead: dict):
        """Update the index with a new/updated bead."""
        index_file = self.beads_dir / INDEX_FILE
        index = self._read_json(index_file)
        
        index["beads"][bead["id"]] = bead
        index["stats"]["total_beads"] = len(index["beads"])
        
        self._write_json(index_file, index)
