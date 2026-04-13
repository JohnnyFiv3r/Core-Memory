"""Core Memory Demo Studio — Web UI

FastAPI demo that exposes canonical memory behavior across five surfaces:
  - Chat
  - Memory
  - Claims
  - Runtime
  - Benchmark

Benchmark runs are isolated (clean/snapshot temp roots) and do not mutate the
live demo store.

Usage:
    python demo/app.py
    # Or with a specific model:
    python demo/app.py --model openai:gpt-4o
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

# Load .env from repo root before anything else
try:
    from dotenv import load_dotenv  # type: ignore
    _DOTENV_IMPORT_ERROR: Exception | None = None
except Exception as _exc:  # pragma: no cover - startup environment specific
    _DOTENV_IMPORT_ERROR = _exc

    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:  # type: ignore
        return False

_DEMO_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _load_demo_env() -> None:
    """Load repo-root `.env` into the process.

    If the parent environment sets `OPENAI_API_KEY` (or similar) to an **empty**
    string — common with `docker exec -e OPENAI_API_KEY=$VAR` when `$VAR` is unset —
    `load_dotenv` would not override it by default, and providers see "no key".
    Treat blank values as unset so `.env` can supply the real key.
    """
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(key, "").strip() == "":
            os.environ.pop(key, None)
    load_dotenv(_DEMO_ENV_PATH)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        raw = os.environ.get(key)
        if raw is None:
            continue
        v = raw.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1].strip()
        if v:
            os.environ[key] = v
        else:
            os.environ.pop(key, None)


_load_demo_env()

# Enable auto-promotion on compact so flush promotes qualifying beads
os.environ.setdefault("CORE_MEMORY_AUTO_PROMOTE_ON_COMPACT", "1")

_WEB_IMPORT_ERROR: Exception | None = None
try:
    import uvicorn  # type: ignore
    from fastapi import FastAPI, Request  # type: ignore
    from fastapi.responses import HTMLResponse, JSONResponse  # type: ignore
except Exception as _exc:  # pragma: no cover - startup environment specific
    _WEB_IMPORT_ERROR = _exc
    uvicorn = None  # type: ignore
    Request = Any  # type: ignore
    HTMLResponse = Any  # type: ignore

    class JSONResponse(dict):  # type: ignore
        def __init__(self, data: dict, status_code: int = 200):
            super().__init__(data)
            self.status_code = status_code

    class FastAPI:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any):
            pass

        def get(self, *args: Any, **kwargs: Any):
            def _decorator(fn):
                return fn

            return _decorator

        def post(self, *args: Any, **kwargs: Any):
            def _decorator(fn):
                return fn

            return _decorator

# Add parent to path so we can import core_memory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core_memory.persistence.store import MemoryStore
from core_memory.write_pipeline.continuity_injection import load_continuity_injection
from core_memory.claim.resolver import resolve_all_current_state
from core_memory.runtime.jobs import async_jobs_status
from core_memory.runtime.myelination import myelination_report
from core_memory.retrieval.semantic_index import semantic_doctor
from core_memory.retrieval.tools import memory as memory_tools
from core_memory.integrations.api import hydrate_bead_sources
from core_memory.integrations.pydanticai import (
    continuity_prompt,
    memory_execute_tool,
    flush_session,
    memory_search_tool,
    memory_trace_tool,
    run_with_memory,
)
from benchmarks.locomo_like.runner import run_benchmark

logger = logging.getLogger(__name__)

# ── Session Coordinator ───────────────────────────────────────────────
# PydanticAI is run-centric, not session-lifecycle-centric.
# This coordinator owns the session boundary that Core Memory needs:
#   - tracks token budget across turns
#   - auto-flushes at 80% context capacity
#   - manages session ID rotation


class SessionCoordinator:
    """App-layer session lifecycle manager.

    Owns the concept of "session" that PydanticAI doesn't provide natively.
    Triggers flush_session at app-defined boundaries:
      - explicit user action (flush button)
      - context token budget threshold (auto-flush at 80%)
    """

    def __init__(
        self,
        root: str,
        context_budget: int = 10000,
        flush_threshold: float = 0.80,
        token_budget: int = 3000,
        max_beads: int = 80,
    ):
        self.root = root
        self.context_budget = context_budget
        self.flush_threshold = flush_threshold
        self.token_budget = token_budget
        self.max_beads = max_beads

        self.session_id = f"demo-{uuid.uuid4().hex[:8]}"
        self.turn_counter = 0
        self.token_usage = 0

    def next_turn_id(self) -> str:
        self.turn_counter += 1
        return f"t-{self.turn_counter:03d}"

    def record_turn_tokens(self, user_query: str, assistant_response: str) -> None:
        """Estimate tokens consumed by this turn.

        Counts user + assistant text, plus the continuity injection and system
        prompt overhead.  Uses ~4 chars/token as a rough estimator.
        """
        turn_text = len(user_query) + len(assistant_response)
        # Include continuity injection cost (loaded each turn)
        try:
            ctx = load_continuity_injection(self.root)
            records = ctx.get("records") or []
            continuity_chars = sum(
                len(str(r.get("title", ""))) + len(str(r.get("summary", ""))) + len(str(r.get("detail", "")))
                for r in records
            )
        except Exception:
            continuity_chars = 0
        # System prompt is ~300 chars, tool schemas ~200 chars
        overhead = 500
        self.token_usage += (turn_text + continuity_chars + overhead) // 4

    def should_auto_flush(self) -> bool:
        return self.token_usage >= int(self.context_budget * self.flush_threshold)

    def do_flush(self) -> dict:
        """Run canonical session flush: archive, compress, rebuild rolling window."""
        old_session = self.session_id

        result = flush_session(
            root=self.root,
            session_id=self.session_id,
            promote=True,
            token_budget=self.token_budget,
            max_beads=self.max_beads,
        )

        self.session_id = f"demo-{uuid.uuid4().hex[:8]}"
        self.turn_counter = 0
        self.token_usage = 0

        return {
            "flushed_session": old_session,
            "new_session": self.session_id,
            "flush_result": result,
        }


# ── Globals ───────────────────────────────────────────────────────────

MEMORY_ROOT = str(Path(__file__).resolve().parent / "memory_store")
AGENT = None
COORDINATOR: SessionCoordinator | None = None
LAST_TURN_DIAGNOSTICS: dict[str, Any] = {}
LAST_BENCHMARK_REPORT: dict[str, Any] = {}
LAST_BENCHMARK_SUMMARY: dict[str, Any] = {}
LAST_FLUSH_EVENT: dict[str, Any] = {}

app = FastAPI(title="Core Memory Demo")


# ── Helpers ───────────────────────────────────────────────────────────


def _get_coordinator() -> SessionCoordinator:
    global COORDINATOR
    if COORDINATOR is None:
        Path(MEMORY_ROOT).mkdir(parents=True, exist_ok=True)
        COORDINATOR = SessionCoordinator(root=MEMORY_ROOT)
    return COORDINATOR

def detect_model() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic:claude-sonnet-4-20250514"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai:gpt-4o"
    return ""


def create_agent(model_id: str):
    from pydantic_ai import Agent

    agent = Agent(
        model_id,
        system_prompt=(
            "You are a helpful project assistant. You have access to the team's "
            "persistent memory — decisions, lessons, goals, and context from prior "
            "conversations. Use your memory tools proactively to ground your answers "
            "in what the team has recorded. Be specific and cite what you find. "
            "Tool policy: call execute_memory_request first for recall questions; "
            "use search_memory as a secondary check; use trace_memory for "
            "explicit causal trace questions. Do not claim memory is missing unless "
            "both execute and search return no anchors/results."
        ),
        tools=[
            memory_execute_tool(root=MEMORY_ROOT),
            memory_search_tool(root=MEMORY_ROOT),
            memory_trace_tool(root=MEMORY_ROOT),
        ],
    )

    @agent.system_prompt
    def inject_memory():
        sid = _get_coordinator().session_id
        return continuity_prompt(root=MEMORY_ROOT, session_id=sid)

    return agent


def get_memory_state(*, as_of: str | None = None) -> dict:
    """Inspector state snapshot.

    Includes graph-like beads/associations plus canonical read-model views:
    - claim current-state projection
    - continuity injection
    - runtime queue + semantic backend diagnostics
    """
    coordinator = _get_coordinator()

    try:
        store = MemoryStore(root=MEMORY_ROOT)
        index_path = store.beads_dir / "index.json"
        index = store._read_json(index_path) if index_path.exists() else {}
    except Exception as exc:
        return {
            "session": {
                "session_id": coordinator.session_id,
                "token_usage": coordinator.token_usage,
                "context_budget": coordinator.context_budget,
            },
            "memory": {"beads": [], "associations": [], "rolling_window": []},
            "claims": {
                "slots": [],
                "counts": {"active": 0, "conflict": 0, "retracted": 0, "historical": 0, "other": 0},
                "as_of": as_of or None,
            },
            "runtime": {
                "queue": {},
                "semantic_backend": {},
                "last_flush": dict(LAST_FLUSH_EVENT or {}),
                "myelination": {},
                "error": str(exc),
            },
            "last_turn": dict(LAST_TURN_DIAGNOSTICS or {}),
            "benchmark": {"last_summary": dict(LAST_BENCHMARK_SUMMARY or {}), "has_last_report": bool(LAST_BENCHMARK_REPORT)},
            "beads": [],
            "associations": [],
            "rolling_window": [],
            "claim_state": [],
            "stats": {
                "total_beads": 0,
                "total_associations": 0,
                "rolling_window_size": 0,
                "claim_slot_count": 0,
                "session_id": coordinator.session_id,
                "token_usage": coordinator.token_usage,
                "context_budget": coordinator.context_budget,
            },
        }
    beads_map = dict(index.get("beads") or {})

    beads = []
    for b in sorted(beads_map.values(), key=lambda x: x.get("created_at", ""), reverse=True):
        beads.append(
            {
                "id": b.get("id", ""),
                "type": b.get("type", ""),
                "title": b.get("title", ""),
                "summary": b.get("summary", []),
                "status": b.get("status", "open"),
                "session_id": b.get("session_id", ""),
                "source_turn_ids": b.get("source_turn_ids", []),
                "created_at": b.get("created_at", ""),
                "detail": b.get("detail", ""),
                "interaction_role": b.get("interaction_role", ""),
                "memory_outcome": b.get("memory_outcome", ""),
                "claims_count": len(list(b.get("claims") or [])),
                "claim_updates_count": len(list(b.get("claim_updates") or [])),
                "hydrate_available": bool(list(b.get("source_turn_ids") or [])),
            }
        )

    associations = []
    for a in (index.get("associations") or []):
        associations.append(
            {
                "id": a.get("id", ""),
                "source_bead": a.get("source_bead", ""),
                "target_bead": a.get("target_bead", ""),
                "relationship": a.get("relationship", ""),
                "explanation": a.get("explanation", ""),
                "confidence": a.get("confidence", 0),
            }
        )

    try:
        ctx = load_continuity_injection(MEMORY_ROOT)
        rolling = ctx.get("records") or []
    except Exception:
        rolling = []

    claim_state_rows: list[dict[str, Any]] = []
    claim_counts = {"active": 0, "conflict": 0, "retracted": 0, "historical": 0, "other": 0}
    try:
        state = resolve_all_current_state(MEMORY_ROOT, as_of=as_of)
        for slot_key, row in sorted((state.get("slots") or {}).items(), key=lambda kv: str(kv[0])):
            rr = dict(row or {})
            current = dict(rr.get("current_claim") or {})
            status = str(rr.get("status") or "not_found")
            claim_state_rows.append(
                {
                    "slot_key": str(slot_key),
                    "status": status,
                    "value": current.get("value"),
                    "confidence": current.get("confidence"),
                    "claim_id": current.get("id"),
                    "conflict_count": len(list(rr.get("conflicts") or [])),
                    "history_count": len(list(rr.get("history") or [])),
                    "timeline_count": len(list(rr.get("timeline") or [])),
                }
            )
            if status in claim_counts:
                claim_counts[status] += 1
            elif status == "not_found":
                pass
            else:
                claim_counts["other"] += 1
    except Exception:
        claim_state_rows = []

    runtime = {
        "queue": async_jobs_status(root=MEMORY_ROOT),
        "semantic_backend": semantic_doctor(Path(MEMORY_ROOT)),
        "last_flush": dict(LAST_FLUSH_EVENT or {}),
        "myelination": myelination_report(MEMORY_ROOT, since="30d", limit=1000, top=5),
    }

    state_payload = {
        "session": {
            "session_id": coordinator.session_id,
            "token_usage": coordinator.token_usage,
            "context_budget": coordinator.context_budget,
        },
        "memory": {
            "beads": beads,
            "associations": associations,
            "rolling_window": [{"title": r.get("title", ""), "type": r.get("type", "")} for r in rolling],
        },
        "claims": {
            "slots": claim_state_rows,
            "counts": claim_counts,
            "as_of": as_of or None,
        },
        "runtime": runtime,
        "last_turn": dict(LAST_TURN_DIAGNOSTICS or {}),
        "benchmark": {
            "last_summary": dict(LAST_BENCHMARK_SUMMARY or {}),
            "has_last_report": bool(LAST_BENCHMARK_REPORT),
        },
    }

    # Backward-compat fields for existing frontend consumers.
    state_payload.update(
        {
            "beads": beads,
            "associations": associations,
            "rolling_window": state_payload["memory"]["rolling_window"],
            "claim_state": claim_state_rows,
            "stats": {
                "total_beads": len(beads),
                "total_associations": len(associations),
                "rolling_window_size": len(rolling),
                "claim_slot_count": len(claim_state_rows),
                "session_id": coordinator.session_id,
                "token_usage": coordinator.token_usage,
                "context_budget": coordinator.context_budget,
            },
        }
    )

    return state_payload


def _build_preload_turns_file_from_demo(*, max_turns: int = 200) -> str:
    """Create a temporary JSONL preload file from demo turn records.

    This bridges live demo activity into benchmark preload context.
    """
    turns_dir = Path(MEMORY_ROOT) / ".turns"
    if not turns_dir.exists():
        return ""

    rows: list[dict[str, Any]] = []
    for p in sorted(turns_dir.glob("*.jsonl")):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                raw = line.strip()
                if not raw:
                    continue
                rec = json.loads(raw)
                if not isinstance(rec, dict):
                    continue
                uq = str(rec.get("user_query") or "").strip()
                af = str(rec.get("assistant_final") or "").strip()
                if not uq or not af:
                    continue
                rows.append(
                    {
                        "session_id": str(rec.get("session_id") or "demo"),
                        "turn_id": str(rec.get("turn_id") or f"demo-{len(rows)+1}"),
                        "user_query": uq[:500],
                        "assistant_final": af[:900],
                        "origin": "DEMO_PRELOAD",
                    }
                )
        except Exception:
            continue

    if not rows:
        return ""

    rows = rows[-max(1, int(max_turns)) :]
    fd, path = tempfile.mkstemp(prefix="demo-preload-", suffix=".jsonl")
    os.close(fd)
    out = Path(path)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return str(out)


def _answer_diagnostics_for_query(query: str) -> dict[str, Any]:
    q = str(query or "").strip()
    if not q:
        return {}
    try:
        out = memory_tools.execute(
            {
                "raw_query": q,
                "intent": "remember",
                "k": 5,
                "constraints": {"require_structural": False},
            },
            root=MEMORY_ROOT,
            explain=True,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    rows = list(out.get("results") or [])
    top_ids = [str(r.get("bead_id") or "") for r in rows[:5] if str(r.get("bead_id") or "")]
    return {
        "ok": bool(out.get("ok", True)),
        "answer_outcome": str(out.get("answer_outcome") or ""),
        "retrieval_mode": str(out.get("retrieval_mode") or ""),
        "source_surface": str((rows[0] or {}).get("source_surface") or "") if rows else "",
        "anchor_reason": str((rows[0] or {}).get("anchor_reason") or "") if rows else "",
        "result_count": int(len(rows)),
        "top_bead_ids": top_ids,
        "chain_count": int(len(list(out.get("chains") or []))),
        "warnings": list(out.get("warnings") or []),
    }


# ── API Routes ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.post("/api/chat")
async def chat(request: Request):
    global LAST_TURN_DIAGNOSTICS
    assert AGENT is not None
    coordinator = _get_coordinator()

    body = await request.json()
    user_message = body.get("message", "").strip()
    if not user_message:
        return JSONResponse({"error": "Empty message"}, status_code=400)

    turn_id = coordinator.next_turn_id()
    auto_flushed = False
    turn_ok = False

    try:
        result = await run_with_memory(
            AGENT,
            user_message,
            root=MEMORY_ROOT,
            session_id=coordinator.session_id,
            turn_id=turn_id,
        )
        assistant_text = result.output if hasattr(result, "output") else str(result.data)
        turn_ok = True
    except Exception as exc:
        assistant_text = f"Error: {exc}"

    if turn_ok:
        coordinator.record_turn_tokens(user_message, assistant_text)

    # Auto-flush if context budget threshold exceeded
    if coordinator.should_auto_flush():
        try:
            flush_result = coordinator.do_flush()
            auto_flushed = True
            logger.info("auto-flush triggered: %s", flush_result)
        except Exception as exc:
            logger.warning("auto-flush failed: %s", exc)

    diagnostics = _answer_diagnostics_for_query(user_message) if turn_ok else {"ok": False}
    LAST_TURN_DIAGNOSTICS = {
        "turn_id": turn_id,
        "session_id": coordinator.session_id,
        "diagnostics": diagnostics,
    }

    return JSONResponse({
        "response": assistant_text,
        "turn_id": turn_id,
        "session_id": coordinator.session_id,
        "auto_flushed": auto_flushed,
        "last_answer": diagnostics,
    })


@app.get("/api/memory")
async def memory_state():
    return JSONResponse(get_memory_state())


@app.get("/api/demo/state")
async def demo_state_endpoint(request: Request):
    as_of = str(request.query_params.get("as_of") or "").strip() or None
    return JSONResponse(get_memory_state(as_of=as_of))


@app.get("/api/demo/claims")
async def demo_claims_endpoint(request: Request):
    as_of = str(request.query_params.get("as_of") or "").strip() or None
    state = get_memory_state(as_of=as_of)
    return JSONResponse(
        {
            "ok": True,
            "claims": dict(state.get("claims") or {}),
            "session": dict(state.get("session") or {}),
        }
    )


@app.get("/api/demo/runtime")
async def demo_runtime_endpoint():
    state = get_memory_state()
    return JSONResponse(
        {
            "ok": True,
            "runtime": dict(state.get("runtime") or {}),
            "session": dict(state.get("session") or {}),
            "last_turn": dict(state.get("last_turn") or {}),
        }
    )


@app.get("/api/demo/benchmark/last")
async def demo_benchmark_last_endpoint():
    return JSONResponse(
        {
            "ok": bool(LAST_BENCHMARK_REPORT),
            "summary": dict(LAST_BENCHMARK_SUMMARY or {}),
            "report": dict(LAST_BENCHMARK_REPORT or {}),
        }
    )


@app.get("/api/demo/bead/{bead_id}")
async def demo_bead_endpoint(bead_id: str):
    return await get_bead(bead_id)


@app.get("/api/demo/bead/{bead_id}/hydrate")
async def demo_bead_hydrate_endpoint(bead_id: str):
    try:
        out = hydrate_bead_sources(root=MEMORY_ROOT, bead_ids=[str(bead_id)], include_tools=False, before=0, after=0)
        return JSONResponse({"ok": True, **dict(out or {})})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc), "bead_id": bead_id}, status_code=500)


@app.get("/api/demo/claim-slot/{subject}/{slot}")
async def demo_claim_slot_endpoint(subject: str, slot: str, request: Request):
    key = f"{str(subject).strip()}:{str(slot).strip()}"
    try:
        as_of = str(request.query_params.get("as_of") or "").strip() or None
        state = resolve_all_current_state(MEMORY_ROOT, as_of=as_of)
        row = dict((state.get("slots") or {}).get(key) or {})
        return JSONResponse({"ok": True, "slot_key": key, "row": row, "as_of": as_of})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc), "slot_key": key, "as_of": as_of}, status_code=500)


@app.post("/api/flush")
async def flush_endpoint():
    """Manual session flush: archive, compress, rebuild rolling window."""
    global LAST_FLUSH_EVENT
    coordinator = _get_coordinator()

    try:
        result = coordinator.do_flush()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    payload = {
        "flushed_session": result["flushed_session"],
        "new_session": result["new_session"],
        "flush_ok": result.get("flush_result", {}).get("ok", False),
        "rolling_window_beads": int(len((result.get("flush_result", {}).get("rolling_window") or {}).get("records") or [])),
    }
    LAST_FLUSH_EVENT = dict(payload)
    return JSONResponse(payload)


@app.post("/api/seed")
async def seed_endpoint():
    try:
        _seed_demo_history()
        state = get_memory_state()
        return JSONResponse({"ok": True, "seeded": 5, "stats": state.get("stats") or {}})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/benchmark-run")
async def benchmark_run_endpoint(request: Request):
    """Run LOCOMO-like benchmark from the demo UI.

    Defaults to fast local smoke settings, with optional preload from demo turns.
    """
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}

    subset = str((body or {}).get("subset") or "local").strip() or "local"
    semantic_mode = str((body or {}).get("semantic_mode") or "degraded_allowed").strip() or "degraded_allowed"
    vector_backend = str((body or {}).get("vector_backend") or "local-faiss").strip() or "local-faiss"
    myelination_mode = str((body or {}).get("myelination") or "off").strip() or "off"
    root_mode = str((body or {}).get("root_mode") or "snapshot").strip().lower() or "snapshot"
    if root_mode not in {"snapshot", "clean"}:
        root_mode = "snapshot"
    limit_raw = (body or {}).get("limit")
    limit = int(limit_raw) if isinstance(limit_raw, int) and limit_raw > 0 else None

    preload_from_demo = bool((body or {}).get("preload_from_demo", False))
    preload_turns_max = int((body or {}).get("preload_turns_max") or 200)

    global LAST_BENCHMARK_REPORT, LAST_BENCHMARK_SUMMARY
    preload_file = ""
    benchmark_temp_root = ""
    root_mode_effective = root_mode
    snapshot_copy_warnings: list[str] = []
    try:
        if preload_from_demo:
            preload_file = _build_preload_turns_file_from_demo(max_turns=preload_turns_max)

        # Always isolate benchmark storage from live demo store to avoid contamination.
        benchmark_temp_root = tempfile.mkdtemp(prefix="demo-benchmark-")
        if root_mode == "snapshot":
            src = Path(MEMORY_ROOT)
            dst = Path(benchmark_temp_root)
            try:
                if src.exists():
                    for child in src.iterdir():
                        target = dst / child.name
                        if child.is_dir():
                            shutil.copytree(child, target, dirs_exist_ok=True)
                        else:
                            shutil.copy2(child, target)
            except Exception as exc:
                root_mode_effective = "clean"
                snapshot_copy_warnings.append(f"snapshot_copy_failed: {exc}")

        base = Path(__file__).resolve().parent.parent / "benchmarks" / "locomo_like"
        report = await asyncio.to_thread(
            run_benchmark,
            fixtures_dir=base / "fixtures",
            gold_dir=base / "gold",
            subset=subset,
            limit=limit,
            semantic_mode=semantic_mode,
            vector_backend=vector_backend,
            myelination_mode=myelination_mode,
            preload_turns_file=(Path(preload_file) if preload_file else None),
            benchmark_root=benchmark_temp_root,
        )

        totals = dict(report.get("totals") or {})
        meta = dict(report.get("metadata") or {})
        summary = {
            "cases": int(totals.get("cases") or 0),
            "pass": int(totals.get("pass") or 0),
            "fail": int(totals.get("fail") or 0),
            "accuracy": float(totals.get("accuracy") or 0.0),
            "backend_modes": list(meta.get("benchmark_backend_modes") or []),
            "preload_turn_count": int(meta.get("preload_turn_count") or 0),
            "semantic_mode": str(meta.get("semantic_mode") or ""),
            "root_mode": root_mode_effective,
            "isolated_root": benchmark_temp_root,
            "isolated_run": True,
            "warnings": snapshot_copy_warnings,
        }
        LAST_BENCHMARK_REPORT = dict(report)
        LAST_BENCHMARK_SUMMARY = dict(summary)
        return JSONResponse({"ok": True, "summary": summary, "report": report})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        if benchmark_temp_root:
            try:
                shutil.rmtree(benchmark_temp_root, ignore_errors=True)
            except Exception:
                pass
        if preload_file:
            try:
                Path(preload_file).unlink(missing_ok=True)
            except Exception:
                pass


@app.get("/api/bead/{bead_id}")
async def get_bead(bead_id: str):
    """Look up a specific bead by ID — for the "prove it" moment."""
    store = MemoryStore(root=MEMORY_ROOT)
    index_path = store.beads_dir / "index.json"
    if not index_path.exists():
        return JSONResponse({"error": "No store"}, status_code=404)

    index = store._read_json(index_path)
    bead = (index.get("beads") or {}).get(bead_id)
    if not bead:
        return JSONResponse({"error": "Bead not found"}, status_code=404)
    return JSONResponse(bead)


# ── Seed (CLI-only, for quick demos) ──────────────────────────────────

def _seed_demo_history():
    store = MemoryStore(root=MEMORY_ROOT)
    decision_id = store.add_bead(
        type="decision", title="Chose PostgreSQL over MySQL",
        summary=["JSONB support for flexible schema", "2x faster for our JSON workload", "Mature extension ecosystem"],
        detail="Evaluated MySQL 8, SQLite, and PostgreSQL 16. Ran pgbench and sysbench. PostgreSQL won on JSONB indexing.",
        session_id="s-history", scope="project",
    )
    lesson_id = store.add_bead(
        type="lesson", title="Always benchmark before choosing infrastructure",
        summary=["Synthetic benchmarks misled us before", "Representative workload testing caught a 2x gap"],
        detail="Prior project chose MySQL based on TPC-C. Actual workload was JSON-heavy. This time we benchmarked first.",
        session_id="s-history", scope="project",
    )
    evidence_id = store.add_bead(
        type="evidence", title="Benchmark data: PostgreSQL 2x faster",
        summary=["pgbench and sysbench on representative workload", "Median latency and p95 both improved"],
        detail="Benchmarks showed PostgreSQL ~2x faster than MySQL for JSON-heavy queries.",
        session_id="s-history", scope="project",
    )
    store.add_bead(
        type="goal", title="Migrate authentication to OAuth2",
        summary=["Legal flagged session-token storage", "Deadline: end of Q2 2026", "Support Google + GitHub IdPs"],
        session_id="s-history", scope="project",
    )
    store.add_bead(
        type="decision", title="Adopted FastAPI for HTTP layer",
        summary=["Async-first for I/O-bound workload", "Auto OpenAPI spec", "Native Pydantic validation"],
        detail="Considered Flask, Django REST, FastAPI. Flask lacks async. Django too heavy. FastAPI won.",
        session_id="s-history", scope="project",
    )

    # Add explicit structural links so causal trace can ground "why" answers.
    try:
        store.link(lesson_id, decision_id, "supports", "Benchmarking lesson informed DB decision")
        store.link(evidence_id, decision_id, "supports", "Benchmark evidence supports selected database")
    except Exception:
        # Non-fatal for demo seeding; beads still exist even if links already present.
        pass
    print("  Seeded 5 sample beads + structural links from project history")


# ── Main ──────────────────────────────────────────────────────────────

def main():
    global AGENT, COORDINATOR

    if _WEB_IMPORT_ERROR is not None:
        print("Demo web dependencies are missing.")
        print(f"Import error: {_WEB_IMPORT_ERROR}")
        print("Install with: pip install fastapi uvicorn")
        sys.exit(1)

    if _DOTENV_IMPORT_ERROR is not None:
        print("Optional dependency python-dotenv is missing.")
        print("Install with: pip install python-dotenv")
        print("Continuing without .env auto-load; environment variables must be exported explicitly.")

    parser = argparse.ArgumentParser(description="Core Memory Demo App")
    parser.add_argument("--model", default=None, help="Model ID (auto-detects from env if omitted)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    parser.add_argument("--context-budget", type=int, default=10000, help="Context token budget (default: 10000)")
    parser.add_argument("--seed", action="store_true", help="Pre-populate with sample project history (skips organic bead creation)")
    args = parser.parse_args()

    model_id = args.model or detect_model()
    if not model_id:
        print("No model detected. Set one of:")
        print("  export ANTHROPIC_API_KEY='...'")
        print("  export OPENAI_API_KEY='...'")
        sys.exit(1)

    if model_id.startswith("openai:") and not (os.environ.get("OPENAI_API_KEY") or "").strip():
        print("OPENAI_API_KEY is missing or empty after loading .env.")
        print(f"  Expected file: {_DEMO_ENV_PATH.resolve()}")
        print("  Use one line (no spaces around =): OPENAI_API_KEY=sk-...")
        print("  If you use Docker, this path must be inside the mounted workspace/Core-Memory folder.")
        sys.exit(1)
    if model_id.startswith("anthropic:") and not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        print("ANTHROPIC_API_KEY is missing or empty after loading .env.")
        print(f"  Expected file: {_DEMO_ENV_PATH.resolve()}")
        sys.exit(1)

    try:
        AGENT = create_agent(model_id)
    except ImportError:
        print("pydantic-ai not installed. Run: pip install -e '.[pydanticai]'")
        sys.exit(1)

    Path(MEMORY_ROOT).mkdir(parents=True, exist_ok=True)
    COORDINATOR = SessionCoordinator(root=MEMORY_ROOT, context_budget=args.context_budget)

    if args.seed:
        _seed_demo_history()

    print(f"\n  Core Memory Demo")
    print(f"  Model:    {model_id}")
    print(f"  Memory:   {MEMORY_ROOT}")
    print(f"  Session:  {COORDINATOR.session_id}")
    print(f"  Budget:   {COORDINATOR.context_budget} tokens (auto-flush at {int(COORDINATOR.flush_threshold * 100)}%)")
    print(f"\n  Open http://{args.host}:{args.port}\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
