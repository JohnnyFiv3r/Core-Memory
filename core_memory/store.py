"""
Core-Memory store implementation.

This module contains the MemoryStore class which handles all persistence.
Index-first with event audit log:
- index.json is primary (fast queries)
- Events provide audit trail and rebuild capability
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .models import BeadType, Scope, Status, Authority
from . import events
from .io_utils import store_lock, atomic_write_json, append_jsonl

# Defaults for pip package (separate from live OpenClaw usage)
DEFAULT_ROOT = "./memory"
BEADS_DIR = ".beads"
TURNS_DIR = ".turns"
EVENTS_DIR = ".beads/events"
SESSION_FILE = "session-{id}.jsonl"
INDEX_FILE = "index.json"

# NOTE: durability model
# Archive/event writes happen under a store lock with fsync; index writes are atomic.
# We prefer archive-first for bead persistence so rebuild_index() can recover safely
# from archived JSONL + event logs.


class MemoryStore:
    """
    Persistent causal agent memory with lossless compaction.
    Index-first with event audit log:
    - index.json is the primary source of truth (fast queries)
    - Events are appended to .beads/events/ for audit/rebuild
    
    Usage:
        memory = MemoryStore(root="./memory")
        memory.capture_turn(role="assistant", content="...")
        memory.consolidate(session_id="chat-123")
    """
    
    def __init__(self, root: str = DEFAULT_ROOT):
        """Initialize MemoryStore at the given root directory."""
        self.root = Path(root)
        self.beads_dir = self.root / BEADS_DIR
        self.turns_dir = self.root / TURNS_DIR
        self.metrics_state_file = self.root / ".beads" / "events" / "metrics-state.json"

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
        
        # Ensure directories exist
        self.beads_dir.mkdir(parents=True, exist_ok=True)
        self.turns_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize index if needed
        self._init_index()
    
    def _init_index(self):
        """Initialize the index file if it doesn't exist."""
        index_file = self.beads_dir / INDEX_FILE
        with store_lock(self.root):
            if not index_file.exists():
                self._write_json(index_file, {
                    "beads": {},
                    "associations": [],
                    "stats": {
                        "total_beads": 0,
                        "total_associations": 0,
                        "created_at": datetime.now(timezone.utc).isoformat()
                    }
                })
    
    def _read_json(self, path: Path) -> dict:
        """Read a JSON file."""
        with open(path, 'r') as f:
            return json.load(f)
    
    def _write_json(self, path: Path, data: dict):
        """Write JSON atomically."""
        atomic_write_json(path, data)
    
    def _generate_id(self) -> str:
        """Generate a short random bead ID (UUID-derived, non-ULID)."""
        return f"bead-{uuid.uuid4().hex[:12].upper()}"

    def _tokenize(self, text: str) -> set[str]:
        return {t.lower() for t in (text or "").replace("_", " ").replace("-", " ").split() if len(t) >= 3}

    def _redact_text(self, text: str) -> str:
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

    def compute_failure_signature(self, plan: str) -> str:
        """Compute a stable failure signature hash from a plan string."""
        norm = re.sub(r"\s+", " ", (plan or "").strip().lower())
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

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

    def _validate_bead_fields(self, bead: dict):
        """Lightweight per-type causal validation (backward-compatible)."""
        t = bead.get("type")
        because = bead.get("because") or []
        summary = bead.get("summary") or []
        source_turn_ids = bead.get("source_turn_ids") or []
        detail = (bead.get("detail") or "").strip()

        if t in {"decision", "lesson"}:
            if not because and not summary and not detail:
                raise ValueError(f"{t} beads require rationale: provide --because or summary/detail")

        if t == "evidence":
            if not source_turn_ids and not summary and not detail:
                raise ValueError("evidence beads require provenance: provide --source-turn-ids or summary/detail")

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
        candidates = []
        new_tags = set((bead.get("tags") or []))
        new_tokens = self._tokenize(bead.get("title", "") + " " + " ".join(bead.get("summary", [])))

        prior = [b for b in index.get("beads", {}).values() if b.get("id") != bead.get("id")]
        prior = sorted(prior, key=lambda b: b.get("created_at", ""), reverse=True)[:max_lookback]

        for other in prior:
            score = 0
            shared_tags = sorted(list(new_tags.intersection(set(other.get("tags") or []))))
            if shared_tags:
                score += 3 + min(2, len(shared_tags))

            other_tokens = self._tokenize(other.get("title", "") + " " + " ".join(other.get("summary", [])))
            overlap = len(new_tokens.intersection(other_tokens))
            if overlap:
                score += min(3, overlap)

            if bead.get("session_id") and bead.get("session_id") == other.get("session_id"):
                score += 1

            if score <= 0:
                continue

            relationship = "related"
            if shared_tags:
                relationship = "shared_tag"
            elif bead.get("session_id") and bead.get("session_id") == other.get("session_id"):
                relationship = "follows"

            candidates.append({
                "other_id": other.get("id"),
                "relationship": relationship,
                "score": score,
                "shared_tags": shared_tags,
            })

        candidates = sorted(candidates, key=lambda c: (-c["score"], c["other_id"] or ""))
        return candidates[:top_k]
    
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
        from .models import BeadType, Scope
        
        # Normalize enums to strings
        type_value = self._normalize_enum(type, BeadType)
        scope_value = self._normalize_enum(scope, Scope)
        bead_id = self._generate_id()
        now = datetime.now(timezone.utc).isoformat()
        
        bead = {
            "id": bead_id,
            "type": type_value,
            "created_at": now,
            "session_id": session_id,
            "title": title,
            "summary": summary or [],
            "because": because or [],
            "source_turn_ids": source_turn_ids or [],
            "detail": detail,
            "scope": scope_value,
            "authority": "agent_inferred",
            "confidence": 0.8,
            "tags": tags or [],
            "links": links or {},
            "status": "open",
            "recall_count": 0,
            "last_recalled": None,
            **kwargs
        }

        # conservative secret redaction (high-confidence patterns only)
        bead = self._sanitize_bead_content(bead)

        # stable failure signature for FAILED_HYPOTHESIS beads
        if bead.get("type") == "failed_hypothesis":
            basis = " ".join(bead.get("summary", [])) or bead.get("title", "") or bead.get("detail", "")
            bead["failure_signature"] = self.compute_failure_signature(basis)

        self._validate_bead_fields(bead)

        repeat_failure = False
        decision_conflicts = 0
        unjustified_flips = 0

        with store_lock(self.root):
            # Write to session archive first (durability/rebuild source)
            if session_id:
                bead_file = self.beads_dir / SESSION_FILE.format(id=session_id)
            else:
                bead_file = self.beads_dir / "global.jsonl"
            append_jsonl(bead_file, bead)

            # Update index after durable archive write
            index_file = self.beads_dir / INDEX_FILE
            index = self._read_json(index_file)

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
            candidates = []
            if self.associate_on_add and self.assoc_top_k > 0:
                candidates = self._quick_association_candidates(
                    index,
                    bead,
                    max_lookback=self.assoc_lookback,
                    top_k=self.assoc_top_k,
                )

            bead["association_preview"] = [
                {
                    "bead_id": c["other_id"],
                    "relationship": c["relationship"],
                    "score": c["score"],
                }
                for c in candidates
            ]
            index["beads"][bead["id"]] = bead

            for c in candidates:
                assoc = {
                    "id": f"assoc-{uuid.uuid4().hex[:12].upper()}",
                    "type": "association",
                    "source_bead": bead_id,
                    "target_bead": c["other_id"],
                    "relationship": c["relationship"],
                    "explanation": "auto: quick per-turn lookback",
                    "edge_class": "derived",
                    "created_at": now,
                    "score": c["score"],
                    "shared_tags": c["shared_tags"],
                }
                # de-dup against existing same pair+relationship
                exists = any(
                    a.get("source_bead") == assoc["source_bead"]
                    and a.get("target_bead") == assoc["target_bead"]
                    and a.get("relationship") == assoc["relationship"]
                    for a in index.get("associations", [])
                )
                if exists:
                    continue
                index["associations"].append(assoc)
                events.event_association_created(self.root, assoc, use_lock=False)

            index["associations"] = sorted(
                index.get("associations", []),
                key=lambda a: (a.get("created_at", ""), a.get("id", "")),
            )
            index["stats"]["total_associations"] = len(index.get("associations", []))
            self._write_json(index_file, index)

            # Append audit event (minimal - just id + timestamp for rebuild)
            events.event_bead_created(self.root, session_id, bead_id, now, use_lock=False)

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
    
    def migrate_legacy_store(self, legacy_root: str, backup: bool = True) -> dict:
        """Migrate a legacy mem_beads store into this core_memory store.

        Safe behavior:
        - optional backup of current core index
        - id-preserving import from legacy index + JSONL files
        - deterministic association import order
        - full operation under store lock (admin path)
        """
        legacy = Path(legacy_root)
        legacy_index_path = legacy / "index.json"
        if not legacy_index_path.exists():
            raise FileNotFoundError(f"Legacy index not found: {legacy_index_path}")

        with store_lock(self.root):
            if backup:
                idx_path = self.beads_dir / INDEX_FILE
                if idx_path.exists():
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    backup_path = self.beads_dir / f"index.backup.{stamp}.json"
                    atomic_write_json(backup_path, self._read_json(idx_path))

            legacy_index = json.loads(legacy_index_path.read_text())
            existing = self._read_json(self.beads_dir / INDEX_FILE)

            imported_beads = 0
            for bead_id, rec in sorted(legacy_index.get("beads", {}).items()):
                if bead_id in existing.get("beads", {}):
                    continue

                bead_file = legacy / rec.get("file", "")
                line_no = rec.get("line", 0)
                full = None
                if bead_file.exists():
                    with open(bead_file, "r") as f:
                        for i, line in enumerate(f):
                            if i == line_no:
                                full = json.loads(line)
                                break

                bead = {
                    "id": bead_id,
                    "type": rec.get("type", "context"),
                    "created_at": rec.get("created_at", datetime.now(timezone.utc).isoformat()),
                    "session_id": rec.get("session_id"),
                    "title": rec.get("title", ""),
                    "summary": (full or {}).get("summary", []),
                    "detail": (full or {}).get("detail", ""),
                    "scope": rec.get("scope", "project"),
                    "authority": (full or {}).get("authority", "agent_inferred"),
                    "confidence": (full or {}).get("confidence", 0.8),
                    "tags": rec.get("tags", []),
                    "links": (full or {}).get("links", {}),
                    "status": rec.get("status", "open"),
                    "recall_count": rec.get("recall_count", 0),
                    "last_recalled": rec.get("last_recalled"),
                }
                if "promoted_at" in rec:
                    bead["promoted_at"] = rec["promoted_at"]

                existing["beads"][bead_id] = bead
                imported_beads += 1

            # import edges as associations if available
            edge_file = legacy / "edges.jsonl"
            imported_assocs = 0
            if edge_file.exists():
                existing_assocs = existing.setdefault("associations", [])
                seen = {
                    (a.get("id"), a.get("source_bead"), a.get("target_bead"), a.get("relationship"))
                    for a in existing_assocs
                }
                with open(edge_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        e = json.loads(line)
                        assoc = {
                            "id": e.get("id", f"assoc-{uuid.uuid4().hex[:12].upper()}"),
                            "type": "association",
                            "source_bead": e.get("source_id"),
                            "target_bead": e.get("target_id"),
                            "relationship": e.get("type", "related"),
                            "explanation": "",
                            "created_at": e.get("created_at", datetime.now(timezone.utc).isoformat()),
                        }
                        key = (assoc.get("id"), assoc.get("source_bead"), assoc.get("target_bead"), assoc.get("relationship"))
                        if key in seen:
                            continue
                        existing_assocs.append(assoc)
                        seen.add(key)
                        imported_assocs += 1

                existing["associations"] = sorted(
                    existing.get("associations", []),
                    key=lambda a: (a.get("created_at", ""), a.get("id", "")),
                )

            existing.setdefault("stats", {})["total_beads"] = len(existing.get("beads", {}))
            existing.setdefault("stats", {})["total_associations"] = len(existing.get("associations", []))
            self._write_json(self.beads_dir / INDEX_FILE, existing)

            return {
                "ok": True,
                "legacy_root": str(legacy),
                "imported_beads": imported_beads,
                "imported_associations": imported_assocs,
                "total_beads": len(existing.get("beads", {})),
                "total_associations": len(existing.get("associations", [])),
            }

    def compact(self, session_id: Optional[str] = None, promote: bool = False) -> dict:
        """Core-native compact: archive detail text losslessly and optionally promote."""
        with store_lock(self.root):
            index = self._read_json(self.beads_dir / INDEX_FILE)
            archive_file = self.beads_dir / "archive.jsonl"
            compacted = 0

            for bead_id in sorted(index.get("beads", {}).keys()):
                bead = index["beads"][bead_id]
                if session_id and bead.get("session_id") != session_id:
                    continue
                detail = bead.get("detail", "")
                if detail:
                    archive = {
                        "bead_id": bead_id,
                        "detail": detail,
                        "summary": bead.get("summary", []),
                        "archived_at": datetime.now(timezone.utc).isoformat(),
                    }
                    append_jsonl(archive_file, archive)
                    bead["detail"] = ""
                    compacted += 1
                if promote and bead.get("status") != "promoted":
                    bead["status"] = "promoted"
                    bead["promoted_at"] = datetime.now(timezone.utc).isoformat()
                index["beads"][bead_id] = bead

            self._write_json(self.beads_dir / INDEX_FILE, index)
            return {"ok": True, "compacted": compacted, "session": session_id}

    def uncompact(self, bead_id: str) -> dict:
        """Restore compacted bead detail from core archive."""
        with store_lock(self.root):
            index = self._read_json(self.beads_dir / INDEX_FILE)
            if bead_id not in index.get("beads", {}):
                return {"ok": False, "error": f"Bead not found: {bead_id}"}

            archive_file = self.beads_dir / "archive.jsonl"
            if not archive_file.exists():
                return {"ok": False, "error": f"Bead not found in archive: {bead_id}"}

            found = None
            with open(archive_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if row.get("bead_id") == bead_id:
                        found = row

            if not found:
                return {"ok": False, "error": f"Bead not found in archive: {bead_id}"}

            bead = index["beads"][bead_id]
            bead["detail"] = found.get("detail", "")
            if found.get("summary"):
                bead["summary"] = found.get("summary")
            index["beads"][bead_id] = bead
            self._write_json(self.beads_dir / INDEX_FILE, index)
            return {"ok": True, "id": bead_id}

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
        limit: int = 20
    ) -> list:
        """
        Query beads with filters.
        
        Args:
            type: Filter by bead type (BeadType enum or string)
            status: Filter by status (Status enum or string)
            tags: Filter by tags
            scope: Filter by scope (Scope enum or string)
            limit: Max results
            
        Returns:
            List of matching beads
        """
        from .models import BeadType, Status, Scope
        
        # Normalize enums to strings
        type_filter = self._normalize_enum(type, BeadType)
        status_filter = self._normalize_enum(status, Status)
        scope_filter = self._normalize_enum(scope, Scope)
        
        index = self._read_json(self.beads_dir / INDEX_FILE)
        results = []
        
        for bead_id, bead in index.get("beads", {}).items():
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
    
    def promote(self, bead_id: str) -> bool:
        """
        Promote a bead to long-term memory.
        
        Args:
            bead_id: ID of bead to promote
            
        Returns:
            Success
        """
        with store_lock(self.root):
            index = self._read_json(self.beads_dir / INDEX_FILE)

            if bead_id not in index["beads"]:
                return False

            bead = index["beads"][bead_id]
            bead["status"] = "promoted"
            bead["promoted_at"] = datetime.now(timezone.utc).isoformat()

            index["beads"][bead_id] = bead
            self._write_json(self.beads_dir / INDEX_FILE, index)

            # Append audit event (rebuild support)
            events.event_bead_promoted(self.root, bead_id, use_lock=False)

            return True
    
    def link(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        explanation: str = ""
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
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        with store_lock(self.root):
            index = self._read_json(self.beads_dir / INDEX_FILE)
            index["associations"].append(assoc)
            index["stats"]["total_associations"] += 1
            self._write_json(self.beads_dir / INDEX_FILE, index)

            # Append audit event (rebuild support)
            events.event_association_created(self.root, assoc, use_lock=False)

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
    
    def dream(self) -> list:
        """
        Run Dreamer association analysis.
        
        Returns:
            List of discovered associations
        """
        try:
            from . import dreamer
            # Pass the store instance for decoupled access
            return dreamer.run_analysis(store=self)
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
