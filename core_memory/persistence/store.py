"""
Core-Memory store implementation.

This module contains the MemoryStore class which handles persistence.
Session-first live authority with index projection:
- session JSONL is the live append authority surface
- index.json is a fast projection/cache for retrieval convenience
- events provide audit trail and rebuild capability
"""

import hashlib
import json
import os
import re
import uuid
import shlex
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from ..schema.models import BeadType, Scope, Status, Authority
from ..persistence import events
from ..persistence.io_utils import store_lock, atomic_write_json, append_jsonl
from ..persistence.archive_index import append_archive_snapshot, read_snapshot, rebuild_archive_index
from ..runtime.session_surface import read_session_surface
from ..runtime.turn_archive import find_turn_record
from ..policy.promotion_contract import validate_transition, classify_signal, is_promotion_locked, current_promotion_state
from ..retrieval.query_norm import _tokenize, _is_memory_intent, _expand_query_tokens
from ..retrieval.lifecycle import mark_semantic_dirty, mark_trace_dirty
from ..retrieval.failure_patterns import compute_failure_signature, find_failure_signature_matches, preflight_failure_check
from ..policy.hygiene import _redact_text, sanitize_bead_content, extract_constraints, enforce_bead_hygiene_contract
from ..policy.promotion import compute_promotion_score, compute_adaptive_threshold, is_candidate_promotable, get_recommendation_rows

# Defaults for pip package (separate from live OpenClaw usage)
DEFAULT_ROOT = "."
VERSION = "1.1.0"


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
        """Conservative secret redaction for high-confidence credential patterns only."""
        if not text:
            return text

        patterns = [
            (r"github_pat_[A-Za-z0-9_]{20,}", "github_pat"),
            (r"ghp_[A-Za-z0-9]{20,}", "github_pat_classic"),
            (r"x-access-token:[^\s@]{12,}", "x_access_token"),
            (r"AKIA[0-9A-Z]{16}", "aws_access_key_id"),
            (r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b", "bearer_token"),
        ]

        redacted = text
        for pattern, kind in patterns:
            def repl(m):
                h = hashlib.sha256(m.group(0).encode("utf-8")).hexdigest()[:10]
                return f"[REDACTED_SECRET:{kind}:{h}]"
            redacted = re.sub(pattern, repl, redacted)

        return redacted

    def _sanitize_bead_content(self, bead: dict) -> dict:
        bead["title"] = self._redact_text(bead.get("title", ""))
        bead["detail"] = self._redact_text(bead.get("detail", ""))
        bead["summary"] = [self._redact_text(str(s)) for s in (bead.get("summary") or [])]
        bead["because"] = [self._redact_text(str(s)) for s in (bead.get("because") or [])]
        return bead

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
        """Return active constraints from decision/design_principle/goal beads.

        Advisory source set for planner compliance checks.
        """
        index = self._read_json(self.beads_dir / INDEX_FILE)
        beads = list(index.get("beads", {}).values())
        beads = sorted(beads, key=lambda b: b.get("created_at", ""), reverse=True)
        rows: list[dict] = []
        for b in beads:
            if str(b.get("status", "")).lower() in {"superseded"}:
                continue
            if b.get("type") not in {"decision", "design_principle", "goal"}:
                continue
            constraints = b.get("constraints") or []
            if not constraints:
                # Avoid extracting policy from raw sidecar narrative noise unless explicitly set.
                tags = set([str(t).strip().lower() for t in (b.get("tags") or [])])
                if "sidecar" in tags and "turn-finalized" in tags:
                    continue
                text = " ".join([b.get("title", "")] + list(b.get("summary") or []))
                constraints = self.extract_constraints(text)
            if not constraints:
                continue
            rows.append(
                {
                    "bead_id": b.get("id"),
                    "type": b.get("type"),
                    "title": b.get("title"),
                    "constraints": constraints[:5],
                    "created_at": b.get("created_at"),
                }
            )
            if len(rows) >= max(1, int(limit)):
                break
        return rows

    def check_plan_constraints(self, plan: str, limit: int = 20) -> dict:
        """Advisory compliance check: map active constraints to satisfied/violated/unknown.

        Heuristic only; no hard enforcement in Phase 3.
        """
        plan_text = (plan or "").lower().strip()
        plan_tokens = set(shlex.split(plan_text)) if plan_text else set()
        active = self.active_constraints(limit=limit)
        satisfied = []
        violated = []
        unknown = []

        def _hits(constraint: str) -> bool:
            c = re.sub(r"\s+", " ", constraint.lower()).strip()
            if not c:
                return False
            # token overlap heuristic
            ctoks = set([t for t in re.findall(r"[a-z0-9_\-]+", c) if len(t) > 2])
            if not ctoks:
                return False
            return len(ctoks.intersection(plan_tokens)) >= max(1, min(2, len(ctoks) // 3))

        for row in active:
            row_s = {"bead_id": row["bead_id"], "title": row["title"], "constraints": []}
            row_v = {"bead_id": row["bead_id"], "title": row["title"], "constraints": []}
            row_u = {"bead_id": row["bead_id"], "title": row["title"], "constraints": []}
            for c in row.get("constraints", []):
                cl = c.lower()
                has_not = any(x in cl for x in ["must not", "never", "do not", "avoid"])
                hit = _hits(c)
                if has_not:
                    if hit:
                        row_v["constraints"].append(c)
                    else:
                        row_s["constraints"].append(c)
                else:
                    if hit:
                        row_s["constraints"].append(c)
                    else:
                        row_u["constraints"].append(c)
            if row_s["constraints"]:
                satisfied.append(row_s)
            if row_v["constraints"]:
                violated.append(row_v)
            if row_u["constraints"]:
                unknown.append(row_u)

        return {
            "ok": True,
            "mode": "advisory",
            "plan": plan,
            "active_constraints": len(active),
            "satisfied": satisfied,
            "violated": violated,
            "unknown": unknown,
            "recommendation": "review" if violated else "proceed",
        }

    def _read_metrics_state(self) -> dict:
        default = {
            "current": {
                "run_id": None,
                "task_id": None,
                "mode": "core_memory",
                "phase": "core_memory",
                "steps": 0,
                "tool_calls": 0,
                "turns_processed": 0,
                "beads_created": 0,
                "beads_recalled": 0,
            }
        }
        if not self.metrics_state_file.exists():
            return default
        try:
            data = json.loads(self.metrics_state_file.read_text(encoding="utf-8"))
            data.setdefault("current", {})
            for k, v in default["current"].items():
                data["current"].setdefault(k, v)
            return data
        except json.JSONDecodeError:
            return default

    def _write_metrics_state(self, state: dict):
        self.metrics_state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.metrics_state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(self.metrics_state_file)

    def start_task_run(self, run_id: str, task_id: str, mode: str = "core_memory", phase: str = "core_memory") -> dict:
        """Start/reset current metrics run context for step/tool aggregation."""
        with store_lock(self.root):
            state = self._read_metrics_state()
            state["current"] = {
                "run_id": run_id,
                "task_id": task_id,
                "mode": mode,
                "phase": phase,
                "steps": 0,
                "tool_calls": 0,
                "turns_processed": 0,
                "beads_created": 0,
                "beads_recalled": 0,
            }
            self._write_metrics_state(state)
            return state["current"]

    def track_step(self, count: int = 1) -> dict:
        with store_lock(self.root):
            state = self._read_metrics_state()
            state.setdefault("current", {}).setdefault("steps", 0)
            state["current"]["steps"] += max(0, int(count))
            self._write_metrics_state(state)
            return state["current"]

    def track_tool_call(self, count: int = 1) -> dict:
        with store_lock(self.root):
            state = self._read_metrics_state()
            state.setdefault("current", {}).setdefault("tool_calls", 0)
            state["current"]["tool_calls"] += max(0, int(count))
            self._write_metrics_state(state)
            return state["current"]

    def track_turn_processed(self, count: int = 1) -> dict:
        with store_lock(self.root):
            state = self._read_metrics_state()
            state.setdefault("current", {}).setdefault("turns_processed", 0)
            state["current"]["turns_processed"] += max(0, int(count))
            self._write_metrics_state(state)
            return state["current"]

    def track_bead_created(self, count: int = 1) -> dict:
        with store_lock(self.root):
            state = self._read_metrics_state()
            state.setdefault("current", {}).setdefault("beads_created", 0)
            state["current"]["beads_created"] += max(0, int(count))
            self._write_metrics_state(state)
            return state["current"]

    def track_bead_recalled(self, count: int = 1) -> dict:
        with store_lock(self.root):
            state = self._read_metrics_state()
            state.setdefault("current", {}).setdefault("beads_recalled", 0)
            state["current"]["beads_recalled"] += max(0, int(count))
            self._write_metrics_state(state)
            return state["current"]

    def current_run_metrics(self) -> dict:
        with store_lock(self.root):
            return self._read_metrics_state().get("current", {})

    def finalize_task_run(self, result: str = "success", **extra) -> dict:
        """Append final KPI row using current counters and derived compression ratio."""
        cur = self.current_run_metrics()
        turns = int(cur.get("turns_processed", 0) or 0)
        beads_created = int(cur.get("beads_created", 0) or 0)
        compression_ratio = (turns / beads_created) if beads_created > 0 else 0.0
        rec = {
            "run_id": cur.get("run_id"),
            "task_id": cur.get("task_id"),
            "mode": cur.get("mode"),
            "phase": cur.get("phase"),
            "result": result,
            "steps": cur.get("steps", 0),
            "tool_calls": cur.get("tool_calls", 0),
            "beads_created": beads_created,
            "beads_recalled": int(cur.get("beads_recalled", 0) or 0),
            "turns_processed": turns,
            "compression_ratio": compression_ratio,
        }
        rec.update(extra)
        return self.append_metric(rec)

    def append_metric(self, record: dict) -> dict:
        """Append a metrics KPI record (v1 schema defaults applied)."""
        now = datetime.now(timezone.utc).isoformat()
        current = self.current_run_metrics()
        m = {
            "ts": record.get("ts", now),
            "run_id": record.get("run_id") or current.get("run_id") or f"run-{uuid.uuid4().hex[:12]}",
            "mode": record.get("mode") or current.get("mode") or "core_memory",
            "task_id": record.get("task_id") or current.get("task_id") or "unknown",
            "result": record.get("result", "success"),
            "steps": int(record.get("steps", current.get("steps", 0)) or 0),
            "tool_calls": int(record.get("tool_calls", current.get("tool_calls", 0)) or 0),
            "beads_created": int(record.get("beads_created", current.get("beads_created", 0)) or 0),
            "beads_recalled": int(record.get("beads_recalled", current.get("beads_recalled", 0)) or 0),
            "repeat_failure": bool(record.get("repeat_failure", False)),
            "decision_conflicts": int(record.get("decision_conflicts", 0) or 0),
            "unjustified_flips": int(record.get("unjustified_flips", 0) or 0),
            "rationale_recall_score": int(record.get("rationale_recall_score", 0) or 0),
            "turns_processed": int(record.get("turns_processed", current.get("turns_processed", 0)) or 0),
            "compression_ratio": float(record.get("compression_ratio", 0) or 0),
            "phase": record.get("phase") or current.get("phase") or "core_memory",
        }
        if m["compression_ratio"] <= 0 and m["beads_created"] > 0 and m["turns_processed"] > 0:
            m["compression_ratio"] = round(m["turns_processed"] / m["beads_created"], 6)

        # pass-through extensibility for KPI fields (phase-specific)
        for k, v in record.items():
            if k.startswith("kpi_") and k not in m:
                m[k] = v

        events.append_metric(self.root, m)
        return m

    def _infer_target_bead_for_question(self, question: str) -> Optional[dict]:
        """Infer target decision bead for a rationale question using token overlap."""
        idx = self._read_json(self.beads_dir / INDEX_FILE)
        q_tokens = self._title_tokens(question or "")
        best = None
        best_score = 0
        for bead in idx.get("beads", {}).values():
            if bead.get("type") != "decision":
                continue
            b_tokens = self._title_tokens(bead.get("title", ""))
            score = len(q_tokens.intersection(b_tokens))
            if score > best_score:
                best_score = score
                best = bead
        return best

    def evaluate_rationale_recall(self, question: str, answer: str, bead_id: Optional[str] = None) -> dict:
        """Deterministic 0/1/2 rationale recall scorer.

        0 = incorrect/no grounding
        1 = partial (either citation or rationale overlap)
        2 = correct bead citation + rationale overlap
        """
        idx = self._read_json(self.beads_dir / INDEX_FILE)
        target = None
        if bead_id:
            target = (idx.get("beads") or {}).get(bead_id)
        if target is None:
            target = self._infer_target_bead_for_question(question)

        if not target:
            return {
                "score": 0,
                "target_bead_id": None,
                "reason": "no_target_bead",
                "cited_ids": [],
                "overlap_tokens": [],
            }

        target_id = target.get("id")
        cited_ids = re.findall(r"bead-[A-Za-z0-9]{8,}", answer or "")
        cited_match = target_id in cited_ids

        rationale_text = " ".join(target.get("because", []))
        rationale_text += " " + (target.get("mechanism") or "")
        rationale_text += " " + " ".join(target.get("summary", []))

        answer_tokens = self._tokenize(answer or "")
        rationale_tokens = self._tokenize(rationale_text)
        overlap = sorted(answer_tokens.intersection(rationale_tokens))

        score = 0
        if cited_match and len(overlap) >= 2:
            score = 2
        elif cited_match or len(overlap) >= 2:
            score = 1

        return {
            "score": score,
            "target_bead_id": target_id,
            "reason": "ok" if score > 0 else "insufficient_grounding",
            "cited_ids": cited_ids,
            "overlap_tokens": overlap[:20],
        }

    def metrics_report(self, since: str = "7d") -> dict:
        """Deterministic metrics aggregation from metrics.jsonl."""
        window_start = None
        m = re.fullmatch(r"(\d+)([dh])", (since or "").strip().lower())
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            delta = timedelta(days=n) if unit == "d" else timedelta(hours=n)
            window_start = datetime.now(timezone.utc) - delta

        rows = []
        for row in events.iter_metrics(self.root) or []:
            ts = row.get("ts")
            if window_start and ts:
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if dt < window_start:
                        continue
                except ValueError:
                    continue
            rows.append(row)

        rows = sorted(rows, key=lambda r: (r.get("ts", ""), r.get("run_id", "")))
        if not rows:
            return {
                "runs": 0,
                "repeat_failure_rate": 0.0,
                "decision_flip_rate": 0.0,
                "median_steps": 0,
                "median_tool_calls": 0,
                "compression_ratio": 0.0,
                "rationale_recall_avg": 0.0,
            }

        def median(values):
            s = sorted(values)
            n = len(s)
            return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

        runs = len(rows)
        repeat_fail = sum(1 for r in rows if r.get("repeat_failure")) / runs
        flips = sum(int(r.get("unjustified_flips", 0) or 0) for r in rows)
        decision_conflicts = sum(int(r.get("decision_conflicts", 0) or 0) for r in rows)
        flip_rate = (flips / decision_conflicts) if decision_conflicts else 0.0

        steps = [int(r.get("steps", 0) or 0) for r in rows]
        tools = [int(r.get("tool_calls", 0) or 0) for r in rows]
        cr = [float(r.get("compression_ratio", 0) or 0) for r in rows if float(r.get("compression_ratio", 0) or 0) > 0]
        rr = [int(r.get("rationale_recall_score", 0) or 0) for r in rows]

        return {
            "runs": runs,
            "repeat_failure_rate": round(repeat_fail, 4),
            "decision_flip_rate": round(flip_rate, 4),
            "median_steps": median(steps),
            "median_tool_calls": median(tools),
            "compression_ratio": round(sum(cr) / len(cr), 4) if cr else 0.0,
            "rationale_recall_avg": round(sum(rr) / len(rr), 4) if rr else 0.0,
        }

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
        """Aggregate autonomy KPIs from metrics stream."""
        window_start = None
        m = re.fullmatch(r"(\d+)([dh])", (since or "").strip().lower())
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            delta = timedelta(days=n) if unit == "d" else timedelta(hours=n)
            window_start = datetime.now(timezone.utc) - delta

        rows = []
        for row in events.iter_metrics(self.root) or []:
            if row.get("task_id") != "autonomy_kpi":
                continue
            ts = row.get("ts")
            if window_start and ts:
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if dt < window_start:
                        continue
                except ValueError:
                    continue
            rows.append(row)

        rows = sorted(rows, key=lambda r: (r.get("ts", ""), r.get("run_id", "")))
        total = len(rows)
        if total == 0:
            return {
                "runs": 0,
                "repeat_failure_rate": 0.0,
                "unjustified_flip_rate": 0.0,
                "constraint_violation_rate": 0.0,
                "wrong_transfer_rate": 0.0,
                "goal_carryover_rate": 0.0,
                "contradiction_resolution_rate": 0.0,
                "contradiction_latency_avg": 0.0,
            }

        def rate(pred):
            return round(sum(1 for r in rows if pred(r)) / total, 4)

        lat = [int(r.get("kpi_contradiction_latency_turns", 0) or 0) for r in rows if r.get("kpi_contradiction_resolved")]
        lat_avg = round(sum(lat) / len(lat), 4) if lat else 0.0

        return {
            "runs": total,
            "repeat_failure_rate": rate(lambda r: bool(r.get("repeat_failure"))),
            "unjustified_flip_rate": rate(lambda r: bool(r.get("unjustified_flips"))),
            "constraint_violation_rate": rate(lambda r: bool(r.get("kpi_constraint_violation"))),
            "wrong_transfer_rate": rate(lambda r: bool(r.get("kpi_wrong_transfer"))),
            "goal_carryover_rate": rate(lambda r: bool(r.get("kpi_goal_carryover"))),
            "contradiction_resolution_rate": rate(lambda r: bool(r.get("kpi_contradiction_resolved"))),
            "contradiction_latency_avg": lat_avg,
        }

    def schema_quality_report(self, write_path: Optional[str] = None) -> dict:
        """Report required-field warnings and promotion gate blockers."""
        index = self._read_json(self.beads_dir / INDEX_FILE)
        beads = list((index.get("beads") or {}).values())

        total_by_type: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        warnings_by_type: dict[str, int] = {}
        warning_keys: dict[str, int] = {}
        promotion_block_reasons: dict[str, int] = {}

        def inc(d: dict[str, int], k: str, n: int = 1):
            d[k] = d.get(k, 0) + n

        for bead in beads:
            t = str(bead.get("type") or "")
            st = str(bead.get("status") or "")
            inc(total_by_type, t)
            inc(status_counts, st)

            for w in (bead.get("validation_warnings") or []):
                inc(warnings_by_type, t)
                inc(warning_keys, str(w))

            if st != "open" or t not in {"decision", "lesson", "outcome", "precedent"}:
                continue

            because = bool(bead.get("because"))
            detail = bool((bead.get("detail") or "").strip())
            has_evidence = self._has_evidence(bead)
            has_link = bool(str(bead.get("linked_bead_id") or "").strip()) or bool(bead.get("links"))

            if t == "decision" and not (because and (has_evidence or detail)):
                inc(promotion_block_reasons, "decision_missing_because_and_evidence_or_detail")
            elif t == "lesson" and not because:
                inc(promotion_block_reasons, "lesson_missing_because")
            elif t == "outcome":
                result = str(bead.get("result") or "").strip().lower()
                if result not in {"resolved", "failed", "partial", "confirmed"}:
                    inc(promotion_block_reasons, "outcome_invalid_result")
                if not (has_link or has_evidence):
                    inc(promotion_block_reasons, "outcome_missing_link_or_evidence")
            elif t == "precedent":
                if not (str(bead.get("condition") or "").strip() and str(bead.get("action") or "").strip()):
                    inc(promotion_block_reasons, "precedent_missing_condition_action")

        report = {
            "ok": True,
            "total_beads": len(beads),
            "status_counts": status_counts,
            "total_by_type": total_by_type,
            "warnings_by_type": warnings_by_type,
            "top_warning_keys": sorted(warning_keys.items(), key=lambda kv: kv[1], reverse=True)[:20],
            "promotion_block_reasons": promotion_block_reasons,
        }

        if write_path:
            out = Path(write_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                f"# Schema Quality Report ({datetime.now(timezone.utc).isoformat()})",
                "",
                f"- Total beads: {report['total_beads']}",
                f"- Status counts: {report['status_counts']}",
                f"- Type counts: {report['total_by_type']}",
                "",
                "## Validation warnings",
                str(report["top_warning_keys"] or "none"),
                "",
                "## Promotion block reasons",
                str(report["promotion_block_reasons"] or "none"),
            ]
            out.write_text("\n".join(lines), encoding="utf-8")
            report["written"] = str(out)

        return report

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
        t = str(bead.get("type") or "").lower()
        priors = {
            "design_principle": 0.72,
            "precedent": 0.7,
            "decision": 0.66,
            "lesson": 0.62,
            "outcome": 0.6,
            "evidence": 0.58,
            "goal": 0.56,
            "context": 0.35,
            "checkpoint": 0.35,
        }
        score = priors.get(t, 0.4)

        has_evidence = self._has_evidence(bead)
        detail_len = len((bead.get("detail") or "").strip())
        has_link = bool(str(bead.get("linked_bead_id") or "").strip()) or bool(bead.get("links"))
        if has_evidence:
            score += 0.12
        if detail_len >= 80:
            score += 0.1
        if has_link:
            score += 0.08

        rs = self._reinforcement_signals(index, bead)
        score += min(0.16, 0.03 * float(rs.get("association_degree", 0)))
        if rs.get("recurrence"):
            score += 0.06
        if rs.get("recalled"):
            score += 0.05
        if rs.get("links_in", 0) > 0:
            score += 0.05

        # outcome coupling boost
        if t == "outcome" and str(bead.get("linked_bead_id") or "").strip():
            score += 0.05

        # age/decay: small freshness bonus only
        created_at = str(bead.get("created_at") or "")
        freshness = 0.0
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
                freshness = 0.05 if age_days <= 2.0 else 0.0
            except ValueError:
                freshness = 0.0
        score += freshness

        score = max(0.0, min(1.0, score))
        return score, {
            "has_evidence": has_evidence,
            "detail_len": detail_len,
            "has_link": has_link,
            "freshness": freshness,
            "reinforcement": rs,
        }

    def _adaptive_promotion_threshold(self, index: dict) -> float:
        beads = list((index.get("beads") or {}).values())
        if not beads:
            return 0.72
        promoted = sum(1 for b in beads if str(b.get("status") or "") == "promoted")
        ratio = promoted / max(1, len(beads))
        thr = 0.72
        if ratio > 0.25:
            thr += min(0.2, (ratio - 0.25) * 0.6)
        return max(0.68, min(0.92, thr))

    def _candidate_promotable(self, index: dict, bead: dict) -> tuple[bool, dict]:
        score, factors = self._promotion_score(index, bead)
        threshold = self._adaptive_promotion_threshold(index)
        reinforcement_count = int((factors.get("reinforcement") or {}).get("count", 0))
        allow = score >= threshold and reinforcement_count >= 1
        reason = "score+reinforcement" if allow else "insufficient_score_or_reinforcement"
        meta = {
            "score": round(score, 4),
            "threshold": round(threshold, 4),
            "reinforcement_count": reinforcement_count,
            "reason": reason,
        }
        return allow, meta

    def _candidate_recommendation_rows(self, index: dict, query_text: str = "") -> tuple[list[dict], float]:
        beads = list((index.get("beads") or {}).values())
        threshold = self._adaptive_promotion_threshold(index)
        q_tokens = self._expand_query_tokens(query_text, self._tokenize(query_text), max_extra=12)

        rows = []
        for bead in beads:
            if str(bead.get("status") or "") != "candidate":
                continue
            score, factors = self._promotion_score(index, bead)
            reinf = int((factors.get("reinforcement") or {}).get("count", 0))
            text_tokens = self._tokenize((bead.get("title") or "") + " " + " ".join(bead.get("summary") or []))
            q_overlap = len(q_tokens.intersection(text_tokens)) if q_tokens else 0
            if score >= threshold and reinf >= 1:
                rec = "strong"
            elif score >= max(0.6, threshold - 0.08):
                rec = "review"
            else:
                rec = "hold"

            rows.append({
                "bead_id": bead.get("id"),
                "type": bead.get("type"),
                "title": bead.get("title"),
                "summary": (bead.get("summary") or [])[:2],
                "promotion_score": round(score, 4),
                "promotion_threshold": round(threshold, 4),
                "recommendation": rec,
                "query_overlap": q_overlap,
                "reinforcement": factors.get("reinforcement") or {},
                "has_evidence": bool(factors.get("has_evidence")),
                "has_link": bool(factors.get("has_link")),
                "detail_len": int(factors.get("detail_len") or 0),
                "created_at": bead.get("created_at"),
            })

        rows = sorted(rows, key=lambda r: (r.get("query_overlap", 0), r.get("promotion_score", 0.0), r.get("created_at") or ""), reverse=True)
        return rows, threshold

    def promotion_slate(self, limit: int = 20, query_text: str = "") -> dict:
        """Build bounded candidate promotion slate with advisory recommendations."""
        index = self._read_json(self.beads_dir / INDEX_FILE)
        rows, threshold = self._candidate_recommendation_rows(index, query_text=query_text)
        return {
            "ok": True,
            "candidate_total": len(rows),
            "adaptive_threshold": round(threshold, 4),
            "query": query_text,
            "results": rows[: max(1, int(limit))],
        }

    def evaluate_candidates(
        self,
        limit: int = 50,
        query_text: str = "",
        auto_archive_hold: bool = False,
        min_age_hours: int = 12,
    ) -> dict:
        """Refresh advisory recommendation fields for candidates (called each turn).

        If auto_archive_hold=True, same-turn archive low-signal hold candidates
        (no reinforcement, no query overlap, age >= min_age_hours).
        """
        with store_lock(self.root):
            index = self._read_json(self.beads_dir / INDEX_FILE)
            rows, threshold = self._candidate_recommendation_rows(index, query_text=query_text)
            now_dt = datetime.now(timezone.utc)
            now = now_dt.isoformat()
            updated = 0
            auto_archived = 0
            decision_log = self.beads_dir / "events" / "promotion-decisions.jsonl"

            for row in rows[: max(1, int(limit))]:
                bid = str(row.get("bead_id") or "")
                bead = (index.get("beads") or {}).get(bid)
                if not bead:
                    continue

                bead["promotion_score"] = row.get("promotion_score")
                bead["promotion_threshold"] = row.get("promotion_threshold")
                bead["promotion_recommendation"] = row.get("recommendation")
                bead["promotion_last_evaluated_at"] = now
                bead["promotion_last_query_overlap"] = row.get("query_overlap", 0)

                rec = str(row.get("recommendation") or "")
                reinf = int((row.get("reinforcement") or {}).get("count", 0))
                q_overlap = int(row.get("query_overlap", 0) or 0)
                age_ok = False
                created = str(bead.get("created_at") or "")
                if created:
                    try:
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        age_ok = ((now_dt - dt).total_seconds() / 3600.0) >= max(0, int(min_age_hours))
                    except ValueError:
                        age_ok = True

                if auto_archive_hold and rec == "hold" and reinf == 0 and q_overlap == 0 and age_ok and str(bead.get("status") or "") == "candidate":
                    revision_id = f"rev-{uuid.uuid4().hex[:12]}"
                    append_archive_snapshot(
                        self.root,
                        {
                            "bead_id": bid,
                            "revision_id": revision_id,
                            "archived_at": now,
                            "archived_from_status": bead.get("status"),
                            "snapshot": dict(bead),
                            "reason": "auto_archive_hold_same_turn",
                        },
                    )
                    bead["archive_ptr"] = {"revision_id": revision_id}
                    bead["detail"] = ""
                    bead["summary"] = (bead.get("summary") or [])[:2]
                    bead["status"] = "archived"
                    bead["demotion_reason"] = "auto_archive_hold_no_reinforcement_same_turn"
                    bead["promotion_decision"] = "archive"
                    bead["promotion_reason"] = "auto_archive_hold_same_turn"
                    bead["promotion_decided_at"] = now
                    append_jsonl(
                        decision_log,
                        {
                            "ts": now,
                            "bead_id": bid,
                            "before_status": "candidate",
                            "after_status": "archived",
                            "decision": "archive",
                            "reason": "auto_archive_hold_same_turn",
                            "considerations": ["no_reinforcement", "no_query_overlap", f"age_hours>={int(min_age_hours)}"],
                        },
                    )
                    auto_archived += 1

                index["beads"][bid] = bead
                updated += 1

            self._write_json(self.beads_dir / INDEX_FILE, index)
            return {
                "ok": True,
                "candidate_total": len(rows),
                "evaluated": updated,
                "auto_archived": auto_archived,
                "adaptive_threshold": round(threshold, 4),
            }

    def decide_promotion(
        self,
        *,
        bead_id: str,
        decision: str,
        reason: str = "",
        considerations: Optional[list[str]] = None,
    ) -> dict:
        """Apply agent-led promotion decision for a bead.

        decision: promote | keep_candidate | archive
        reason: required for promote/archive
        """
        decision_n = str(decision or "").strip().lower()
        if decision_n not in {"promote", "keep_candidate", "archive"}:
            return {"ok": False, "error": "invalid_decision"}

        if decision_n in {"promote", "archive"} and not str(reason or "").strip():
            return {"ok": False, "error": "reason_required_for_promote_or_archive"}

        with store_lock(self.root):
            index = self._read_json(self.beads_dir / INDEX_FILE)
            bead = (index.get("beads") or {}).get(bead_id)
            if not bead:
                return {"ok": False, "error": f"bead_not_found:{bead_id}"}

            before = str(bead.get("status") or "")
            now = datetime.now(timezone.utc).isoformat()

            # Canonical state normalization / policy enforcement.
            current_state = current_promotion_state(bead)
            is_locked = is_promotion_locked(bead)
            ok_transition, err = validate_transition(bead=bead, decision=decision_n)
            if not ok_transition:
                return {"ok": False, "error": err, "bead_id": bead_id, "decision": decision_n}

            # Snapshot advisory recommendation at decision time.
            score, factors = self._promotion_score(index, bead)
            threshold = self._adaptive_promotion_threshold(index)
            reinf = int((factors.get("reinforcement") or {}).get("count", 0))
            if score >= threshold and reinf >= 1:
                recommendation = "strong"
            elif score >= max(0.6, threshold - 0.08):
                recommendation = "review"
            else:
                recommendation = "hold"
            bead["promotion_score"] = round(score, 4)
            bead["promotion_threshold"] = round(threshold, 4)
            bead["promotion_recommendation"] = recommendation

            if decision_n == "promote":
                bead["status"] = "promoted"
                bead["promotion_state"] = "promoted"
                bead["promotion_locked"] = True
                bead["promoted_at"] = now
                bead["promotion_reason"] = str(reason).strip()
                bead["promotion_evidence"] = {
                    "reason": str(reason).strip(),
                    "score": round(score, 4),
                    "threshold": round(threshold, 4),
                }
            elif decision_n == "keep_candidate":
                bead["status"] = "candidate"
                bead["promotion_state"] = "candidate"
                bead["promotion_locked"] = False
            elif decision_n == "archive":
                revision_id = f"rev-{uuid.uuid4().hex[:12]}"
                append_archive_snapshot(
                    self.root,
                    {
                        "bead_id": bead_id,
                        "revision_id": revision_id,
                        "archived_at": now,
                        "archived_from_status": bead.get("status"),
                        "snapshot": dict(bead),
                        "reason": "agent_decision_archive",
                    },
                )
                bead["archive_ptr"] = {"revision_id": revision_id}
                bead["detail"] = ""
                bead["summary"] = (bead.get("summary") or [])[:2]
                bead["status"] = "archived"
                bead["promotion_state"] = "null"
                bead["promotion_locked"] = False
                bead["demotion_reason"] = str(reason).strip()

            bead["promotion_decision"] = decision_n
            bead["promotion_decided_at"] = now
            bead["promotion_decision_turn_id"] = str((bead.get("source_turn_ids") or [""])[-1] or "")
            if considerations:
                bead["promotion_considerations"] = [str(c) for c in considerations][:8]

            index["beads"][bead_id] = bead
            self._write_json(self.beads_dir / INDEX_FILE, index)

            # append audit row
            decision_log = self.beads_dir / "events" / "promotion-decisions.jsonl"
            append_jsonl(
                decision_log,
                {
                    "ts": now,
                    "bead_id": bead_id,
                    "before_status": before,
                    "after_status": bead.get("status"),
                    "decision": decision_n,
                    "reason": str(reason or ""),
                    "considerations": [str(c) for c in (considerations or [])][:8],
                },
            )

            mark_semantic_dirty(self.root, reason="decide_promotion")

            return {
                "ok": True,
                "bead_id": bead_id,
                "before_status": before,
                "after_status": bead.get("status"),
                "decision": decision_n,
            }

    def decide_promotion_bulk(self, decisions: list[dict]) -> dict:
        """Apply a bounded batch of agent promotion decisions."""
        rows = decisions or []
        out = []
        for row in rows[:100]:
            out.append(
                self.decide_promotion(
                    bead_id=str(row.get("bead_id") or row.get("id") or "").strip(),
                    decision=str(row.get("decision") or "").strip(),
                    reason=str(row.get("reason") or "").strip(),
                    considerations=[str(x) for x in (row.get("considerations") or [])],
                )
            )
        return {
            "ok": True,
            "requested": len(rows),
            "applied": len(out),
            "results": out,
        }

    def decide_session_promotion_states(self, *, session_id: str, visible_bead_ids: Optional[list[str]] = None, turn_id: str = "") -> dict:
        """Per-turn session decision pass: promoted|candidate|null for visible beads.

        Promotion is terminal: promoted beads remain locked.
        """
        with store_lock(self.root):
            index = self._read_json(self.beads_dir / INDEX_FILE)
            beads = index.get("beads") or {}

            allowed = set(str(x) for x in (visible_bead_ids or []) if str(x).strip())
            ids = []
            for bid, bead in beads.items():
                if str((bead or {}).get("session_id") or "") != str(session_id):
                    continue
                if allowed and str(bid) not in allowed:
                    continue
                ids.append(str(bid))
            ids.sort(key=lambda bid: (str((beads.get(bid) or {}).get("created_at") or ""), bid))

            now = datetime.now(timezone.utc).isoformat()
            counts = {"promoted": 0, "candidate": 0, "null": 0, "locked": 0, "evaluated": 0}

            for bid in ids:
                bead = beads.get(bid) or {}
                counts["evaluated"] += 1

                status = str(bead.get("status") or "").strip().lower()
                state = current_promotion_state(bead)
                locked = is_promotion_locked(bead)

                if locked:
                    bead["status"] = "promoted"
                    bead["promotion_state"] = "promoted"
                    bead["promotion_locked"] = True
                    bead["promotion_decision"] = "promoted_locked"
                    bead["promotion_decided_at"] = now
                    if turn_id:
                        bead["promotion_decision_turn_id"] = str(turn_id)
                    counts["locked"] += 1
                    counts["promoted"] += 1
                    beads[bid] = bead
                    continue

                decision = classify_signal(bead=bead)

                if decision == "promoted":
                    bead["status"] = "promoted"
                    bead["promotion_state"] = "promoted"
                    bead["promotion_locked"] = True
                    bead["promoted_at"] = str(bead.get("promoted_at") or now)
                    bead["promotion_decision"] = "promote"
                    bead["promotion_decided_at"] = now
                    bead["promotion_reason"] = str(bead.get("promotion_reason") or "session_turn_evidence")
                    bead["promotion_evidence"] = {
                        "reason": bead.get("promotion_reason"),
                    }
                    counts["promoted"] += 1
                elif decision == "candidate":
                    bead["status"] = "candidate"
                    bead["promotion_state"] = "candidate"
                    bead["promotion_locked"] = False
                    bead["promotion_decision"] = "keep_candidate"
                    bead["promotion_decided_at"] = now
                    counts["candidate"] += 1
                else:
                    if str(bead.get("status") or "").strip().lower() not in {"promoted", "archived"}:
                        bead["status"] = "open"
                    bead["promotion_state"] = "null"
                    bead["promotion_locked"] = False
                    bead["promotion_decision"] = "null"
                    bead["promotion_decided_at"] = now
                    counts["null"] += 1

                if turn_id:
                    bead["promotion_decision_turn_id"] = str(turn_id)
                beads[bid] = bead

            index["beads"] = beads
            self._write_json(self.beads_dir / INDEX_FILE, index)
            mark_semantic_dirty(self.root, reason="decide_session_promotion_states")
            return {"ok": True, "session_id": str(session_id), "turn_id": str(turn_id or ""), "counts": counts, "evaluated_bead_ids": ids}

    def promotion_kpis(self, limit: int = 500) -> dict:
        """Report promotion decision volume, reasons, and rec-vs-decision alignment."""
        idx = self._read_json(self.beads_dir / INDEX_FILE)
        beads = idx.get("beads") or {}
        decision_log = self.beads_dir / "events" / "promotion-decisions.jsonl"

        decisions = []
        if decision_log.exists():
            with open(decision_log, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        decisions.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        decisions = decisions[-max(1, int(limit)) :]

        by_decision: dict[str, int] = {}
        reason_hist: dict[str, int] = {}
        aligned = 0
        compared = 0

        for d in decisions:
            dec = str(d.get("decision") or "")
            by_decision[dec] = by_decision.get(dec, 0) + 1
            reason = str(d.get("reason") or "").strip()
            if reason:
                reason_hist[reason] = reason_hist.get(reason, 0) + 1

            bid = str(d.get("bead_id") or "")
            bead = beads.get(bid) or {}
            rec = str(bead.get("promotion_recommendation") or "").strip().lower()
            if rec:
                compared += 1
                # coarse alignment mapping
                if (rec == "strong" and dec == "promote") or (rec == "hold" and dec in {"archive", "keep_candidate"}) or (rec == "review"):
                    aligned += 1

        agreement = round(aligned / compared, 4) if compared else None

        return {
            "ok": True,
            "decision_count": len(decisions),
            "by_decision": by_decision,
            "top_reasons": sorted(reason_hist.items(), key=lambda kv: kv[1], reverse=True)[:20],
            "recommendation_alignment": {
                "compared": compared,
                "aligned": aligned,
                "agreement_rate": agreement,
            },
        }

    def rebalance_promotions(self, apply: bool = False) -> dict:
        """Phase B: score promoted beads and demote weakly-supported promotions."""
        with store_lock(self.root):
            index = self._read_json(self.beads_dir / INDEX_FILE)
            promoted_ids = [bid for bid, b in (index.get("beads") or {}).items() if str(b.get("status") or "") == "promoted"]
            threshold = self._adaptive_promotion_threshold(index)
            demote: list[dict] = []

            for bid in promoted_ids:
                bead = index["beads"][bid]
                score, factors = self._promotion_score(index, bead)
                reinf = int((factors.get("reinforcement") or {}).get("count", 0))
                if score < threshold and reinf == 0 and str(bead.get("type") or "") != "session_end" and str(bead.get("type") or "") != "session_start":
                    demote.append({"bead_id": bid, "score": round(score, 4), "reinforcement": reinf})

            applied = 0
            if apply:
                for row in demote:
                    bid = row["bead_id"]
                    bead = index["beads"].get(bid)
                    if not bead:
                        continue
                    revision_id = f"rev-{uuid.uuid4().hex[:12]}"
                    append_archive_snapshot(self.root, {
                        "bead_id": bid,
                        "revision_id": revision_id,
                        "archived_at": datetime.now(timezone.utc).isoformat(),
                        "archived_from_status": bead.get("status"),
                        "snapshot": dict(bead),
                    })
                    bead["archive_ptr"] = {"revision_id": revision_id}
                    bead["detail"] = ""
                    bead["summary"] = (bead.get("summary") or [])[:2]
                    bead["status"] = "archived"
                    bead["demoted_at"] = datetime.now(timezone.utc).isoformat()
                    bead["demotion_reason"] = "phase_b_rebalance"
                    index["beads"][bid] = bead
                    applied += 1

                self._write_json(self.beads_dir / INDEX_FILE, index)

            return {
                "ok": True,
                "promoted_total": len(promoted_ids),
                "adaptive_threshold": round(threshold, 4),
                "demote_candidates": len(demote),
                "applied": applied,
                "sample": demote[:50],
            }

    def _normalize_links(self, links) -> list[dict]:
        """Normalize links to canonical list[{type, bead_id}] format."""
        if links is None:
            return []
        out: list[dict] = []
        if isinstance(links, list):
            for row in links:
                if not isinstance(row, dict):
                    continue
                ltype = str(row.get("type") or "").strip()
                bid = str(row.get("bead_id") or row.get("id") or "").strip()
                if ltype and bid:
                    out.append({"type": ltype, "bead_id": bid})
            return out
        if isinstance(links, dict):
            for k, v in links.items():
                if isinstance(v, list):
                    for bid in v:
                        b = str(bid or "").strip()
                        if b:
                            out.append({"type": str(k), "bead_id": b})
                else:
                    b = str(v or "").strip()
                    if b:
                        out.append({"type": str(k), "bead_id": b})
        return out

    def _has_evidence(self, bead: dict) -> bool:
        return bool((bead.get("evidence_refs") or []) or (bead.get("tool_output_ids") or []) or (bead.get("tool_output_id") or "").strip())

    def _required_field_issues(self, bead: dict) -> list[str]:
        issues: list[str] = []
        t = str(bead.get("type") or "").strip()
        title = str(bead.get("title") or "").strip()
        summary = bead.get("summary") or []
        session_id = str(bead.get("session_id") or "").strip()
        source_turn_ids = bead.get("source_turn_ids") or []
        status = str(bead.get("status") or "").strip()
        created_at = str(bead.get("created_at") or "").strip()
        because = bead.get("because") or []
        detail = (bead.get("detail") or "").strip()
        links = bead.get("links") or []

        # Global baseline
        if not t:
            issues.append("missing:type")
        if not title:
            issues.append("missing:title")
        if not isinstance(summary, list) or len(summary) == 0:
            issues.append("missing:summary")
        if not session_id:
            issues.append("missing:session_id")
        if not isinstance(source_turn_ids, list) or len(source_turn_ids) == 0:
            issues.append("missing:source_turn_ids")
        if not status:
            issues.append("missing:status")
        if not created_at:
            issues.append("missing:created_at")

        # bounded summary
        if isinstance(summary, list):
            if len(summary) > 3:
                issues.append("bounds:summary>3")
            for s in summary:
                if len(str(s)) > 220:
                    issues.append("bounds:summary_item>220")
                    break

        # type-specific
        has_evidence = self._has_evidence(bead)
        if t == "decision":
            if not (because or has_evidence or detail):
                issues.append("decision:need_because_or_evidence_or_detail")
        elif t == "lesson":
            if not because:
                issues.append("lesson:missing_because")
        elif t == "outcome":
            result = str(bead.get("result") or "").strip().lower()
            if result not in {"resolved", "failed", "partial", "confirmed"}:
                issues.append("outcome:invalid_result")
            linked = str(bead.get("linked_bead_id") or "").strip() or bool(links)
            if not (linked or has_evidence):
                issues.append("outcome:need_link_or_evidence")
        elif t == "evidence":
            supports = bead.get("supports_bead_ids") or []
            if not (has_evidence or len(detail) >= 60):
                issues.append("evidence:need_reference_or_detail")
            if not isinstance(supports, list) or len(supports) == 0:
                issues.append("evidence:missing_supports_bead_ids")
        elif t == "goal":
            if not str(bead.get("goal_id") or "").strip():
                issues.append("goal:missing_goal_id")
            if not str(bead.get("success_criteria") or "").strip():
                issues.append("goal:missing_success_criteria")
        elif t == "precedent":
            if not str(bead.get("condition") or "").strip():
                issues.append("precedent:missing_condition")
            if not str(bead.get("action") or "").strip():
                issues.append("precedent:missing_action")
        elif t == "design_principle":
            if not because:
                issues.append("design_principle:missing_because")
        elif t == "failed_hypothesis":
            tested_by = str(bead.get("tested_by") or "").strip().lower()
            if tested_by and tested_by not in {"tool", "reasoning", "observation"}:
                issues.append("failed_hypothesis:invalid_tested_by")
        elif t == "tool_call":
            if not str(bead.get("tool") or bead.get("capability") or "").strip():
                issues.append("tool_call:missing_tool_or_capability")
            result_status = str(bead.get("tool_result_status") or "").strip().lower()
            if result_status and result_status not in {"success", "failure"}:
                issues.append("tool_call:invalid_tool_result_status")

        return sorted(set(issues))

    def _validate_bead_fields(self, bead: dict):
        """Required-fields validation with warn-first rollout."""
        context_tags = bead.get("context_tags")
        if context_tags is not None:
            if not isinstance(context_tags, list):
                raise ValueError("context_tags must be a list of strings")
            for tag in context_tags:
                if not isinstance(tag, str):
                    raise ValueError("context_tags entries must be strings")

        issues = self._required_field_issues(bead)
        if issues and self.strict_required_fields:
            raise ValueError("required field validation failed: " + ", ".join(issues))
        if issues:
            bead["validation_warnings"] = issues

    def _resolve_bead_session_id(self, *, session_id: Optional[str], source_turn_ids: Optional[list]) -> str:
        sid = str(session_id or "").strip()
        if sid:
            return sid

        mode = self.bead_session_id_mode
        if mode not in {"infer", "strict", "unknown"}:
            mode = "infer"

        inferred = ""
        if mode in {"infer", "strict"}:
            for tid in (source_turn_ids or []):
                t = str(tid or "").strip()
                if not t:
                    continue
                row = find_turn_record(root=self.root, turn_id=t, session_id=None)
                if isinstance(row, dict):
                    inferred = str(row.get("session_id") or "").strip()
                    if inferred:
                        break

        if inferred:
            return inferred

        if mode == "strict":
            raise ValueError("missing:session_id (strict mode; unable to infer from source_turn_ids)")

        # keep schema invariant non-null/non-empty even when unresolved
        return "unknown"

    def _title_tokens(self, text: str) -> set[str]:
        return {t for t in self._tokenize(text) if t not in {"the", "and", "for", "with", "this", "that"}}

    def _is_contradictory_decision(self, a_title: str, b_title: str) -> bool:
        a = (a_title or "").lower()
        b = (b_title or "").lower()
        neg = [" not ", " don't ", " never ", " avoid ", " disable ", " remove "]
        a_neg = any(x in f" {a} " for x in neg)
        b_neg = any(x in f" {b} " for x in neg)
        if a_neg != b_neg:
            return True
        antonym_pairs = [("enable", "disable"), ("use", "avoid"), ("allow", "deny")]
        for p, q in antonym_pairs:
            if (p in a and q in b) or (q in a and p in b):
                return True
        return False

    def _detect_decision_conflicts(self, index: dict, bead: dict) -> tuple[int, int, list[str]]:
        """Heuristic conflict detector for new decision bead.

        Returns: (decision_conflicts, unjustified_flips, conflicting_bead_ids)
        """
        if bead.get("type") != "decision":
            return 0, 0, []

        new_tokens = self._title_tokens(bead.get("title", ""))
        if not new_tokens:
            return 0, 0, []

        conflicts = []
        assocs = index.get("associations", [])

        for prior in index.get("beads", {}).values():
            if prior.get("id") == bead.get("id"):
                continue
            if prior.get("type") != "decision":
                continue
            overlap = len(new_tokens.intersection(self._title_tokens(prior.get("title", ""))))
            if overlap < 2:
                continue
            if not self._is_contradictory_decision(bead.get("title", ""), prior.get("title", "")):
                continue

            # justified if prior already superseded/reversed in history
            prior_id = prior.get("id")
            justified = (prior.get("status") == "superseded") or any(
                (
                    (a.get("source_bead") == prior_id or a.get("target_bead") == prior_id)
                    and a.get("relationship") in {"supersedes", "reversal", "reversed_by"}
                )
                for a in assocs
            )
            if not justified:
                conflicts.append(prior_id)

        if not conflicts:
            return 0, 0, []
        return len(conflicts), 1, sorted(conflicts)

    def _quick_association_candidates(self, index: dict, bead: dict, max_lookback: int = 40, top_k: int = 3) -> list[dict]:
        """Fast, deterministic association inference for newly added beads."""
        from ..association import run_association_pass

        return run_association_pass(index, bead, max_lookback=max_lookback, top_k=top_k)

    def _norm_text(self, s: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", str(s or "").lower()))

    def _bead_similarity(self, a: dict, b: dict) -> float:
        ta = self._norm_text((a.get("title") or "") + " " + " ".join(a.get("summary") or []))
        tb = self._norm_text((b.get("title") or "") + " " + " ".join(b.get("summary") or []))
        sa = set(ta.split())
        sb = set(tb.split())
        if not sa or not sb:
            return 0.0
        inter = len(sa.intersection(sb))
        union = len(sa.union(sb))
        return float(inter) / float(max(1, union))

    def _find_recent_duplicate_bead_id(self, index: dict, bead: dict, session_id: str | None, window: int = 25) -> str | None:
        beads = list((index.get("beads") or {}).values())
        if session_id:
            beads = [b for b in beads if str((b or {}).get("session_id") or "") == str(session_id)]
        beads = sorted(beads, key=lambda b: str((b or {}).get("created_at") or ""), reverse=True)[: max(1, int(window))]

        for prior in beads:
            if str(prior.get("type") or "") != str(bead.get("type") or ""):
                continue
            # exact turn duplicate
            a_turns = set([str(x) for x in (bead.get("source_turn_ids") or []) if str(x)])
            p_turns = set([str(x) for x in (prior.get("source_turn_ids") or []) if str(x)])
            if a_turns and p_turns and a_turns.intersection(p_turns):
                return str(prior.get("id") or "") or None
            sim = self._bead_similarity(bead, prior)
            if sim >= 0.9:
                return str(prior.get("id") or "") or None
        return None
    
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
        """
        Capture a single turn in the session.
        
        Args:
            role: assistant | user | system
            content: The message/response content
            tools_used: List of tools called
            user_message: The user input (for context)
            session_id: Session identifier
        """
        turn = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tools_used": tools_used or []
        }
        
        # Add user message context if provided
        if user_message:
            turn["user_message"] = user_message
        
        # Write to turns directory (separate from beads)
        turn_file = self.turns_dir / SESSION_FILE.format(id=session_id)
        with store_lock(self.root):
            append_jsonl(turn_file, turn)

        self.track_turn_processed(1)
    
    def consolidate(self, session_id: str = "default") -> dict:
        """
        Run session-end consolidation:
        - Summarize session to session_end bead
        - Update rolling window
        - Compact old beads
        
        Args:
            session_id: Session to consolidate
            
        Returns:
            Consolidation summary
        """
        # Read turns for this session
        turn_file = self.turns_dir / SESSION_FILE.format(id=session_id)
        
        if turn_file.exists():
            with open(turn_file, 'r') as f:
                turns = [json.loads(line) for line in f if line.strip()]
            turn_count = len(turns)
        else:
            turn_count = 0
        
        # Read beads for this session
        bead_file = self.beads_dir / SESSION_FILE.format(id=session_id)
        
        if bead_file.exists():
            with open(bead_file, 'r') as f:
                beads = [json.loads(line) for line in f if line.strip()]
            bead_count = len(beads)
        else:
            bead_count = 0
        
        # Create session_end bead
        end_bead_id = self.add_bead(
            type="session_end",
            title=f"Session {session_id} summary",
            summary=[
                f"{turn_count} turns",
                f"{bead_count} events"
            ],
            detail=f"Session {session_id} completed.",
            session_id=session_id,
            scope="project",
            tags=["session", session_id]
        )
        
        return {
            "session_id": session_id,
            "turns": turn_count,
            "events": bead_count,
            "end_bead": end_bead_id
        }

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
        """
        Query beads with filters.
        
        Args:
            type: Filter by bead type (BeadType enum or string)
            status: Filter by status (Status enum or string)
            tags: Filter by tags
            scope: Filter by scope (Scope enum or string)
            limit: Max results
            session_id: Optional session filter; when provided, query uses session surface first
            
        Returns:
            List of matching beads
        """
        from ..schema.models import BeadType, Status, Scope
        
        # Normalize enums to strings
        type_filter = self._normalize_enum(type, BeadType)
        status_filter = self._normalize_enum(status, Status)
        scope_filter = self._normalize_enum(scope, Scope)
        
        results = []

        if session_id:
            # Session-first authority for session-scoped query reads.
            source_rows = list(read_session_surface(self.root, session_id))
            if not source_rows:
                index = self._read_json(self.beads_dir / INDEX_FILE)
                source_rows = [
                    b for b in (index.get("beads") or {}).values()
                    if str((b or {}).get("session_id") or "") == str(session_id)
                ]
            iterable = source_rows
        else:
            index = self._read_json(self.beads_dir / INDEX_FILE)
            iterable = list((index.get("beads") or {}).values())

        for bead in iterable:
            if type_filter and bead.get("type") != type_filter:
                continue
            if status_filter and bead.get("status") != status_filter:
                continue
            if scope_filter and bead.get("scope") != scope_filter:
                continue
            if tags:
                bead_tags = set(bead.get("tags", []))
                if not bead_tags.intersection(set(tags)):
                    continue
            results.append(bead)

            if len(results) >= limit:
                break
        
        return results
    
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
