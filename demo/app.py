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
from datetime import datetime, timezone
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

from core_memory.entity.merge_flow import (
    suggest_entity_merge_proposals,
    decide_entity_merge_proposal,
)
from core_memory.runtime.engine import process_turn_finalized
from core_memory.retrieval.tools import memory as memory_tools
from core_memory.integrations.api import (
    get_turn,
    inspect_state,
    inspect_bead,
    inspect_bead_hydration,
    inspect_claim_slot,
    list_turn_summaries,
)
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

        Counts user + assistant text plus coarse prompt/tool overhead.
        Uses ~4 chars/token as a rough estimator.
        """
        turn_text = len(user_query) + len(assistant_response)
        # System prompt is ~300 chars, tool schemas ~200 chars
        overhead = 500
        self.token_usage += (turn_text + overhead) // 4

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
LAST_BENCHMARK_HISTORY: list[dict[str, Any]] = []
LAST_FLUSH_EVENT: dict[str, Any] = {}
LAST_FLUSH_EVENTS: list[dict[str, Any]] = []

app = FastAPI(title="Core Memory Demo")


# ── Helpers ───────────────────────────────────────────────────────────


def _get_coordinator() -> SessionCoordinator:
    global COORDINATOR
    if COORDINATOR is None:
        Path(MEMORY_ROOT).mkdir(parents=True, exist_ok=True)
        COORDINATOR = SessionCoordinator(root=MEMORY_ROOT)
    return COORDINATOR


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_flush_event(payload: dict[str, Any], *, trigger: str) -> None:
    global LAST_FLUSH_EVENT, LAST_FLUSH_EVENTS
    row = dict(payload or {})
    row["trigger"] = str(trigger or "unknown")
    row["timestamp"] = _utc_now_iso()
    LAST_FLUSH_EVENT = dict(row)
    LAST_FLUSH_EVENTS = ([row] + list(LAST_FLUSH_EVENTS or []))[:20]


def _benchmark_history_path() -> Path:
    return Path(MEMORY_ROOT) / ".demo" / "benchmark-history.jsonl"


def _append_benchmark_history_row(row: dict[str, Any]) -> tuple[bool, str]:
    p = _benchmark_history_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(row or {}), ensure_ascii=False) + "\n")
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _read_benchmark_history(*, limit: int = 20) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    merged.extend([dict(r or {}) for r in list(LAST_BENCHMARK_HISTORY or []) if isinstance(r, dict)])

    p = _benchmark_history_path()
    if p.exists():
        try:
            rows: list[dict[str, Any]] = []
            for line in p.read_text(encoding="utf-8").splitlines():
                raw = str(line or "").strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    rows.append(dict(obj))
            rows.reverse()
            merged.extend(rows)
        except Exception:
            pass

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in merged:
        rid = str(r.get("run_id") or (r.get("summary") or {}).get("run_id") or "")
        key = rid or json.dumps(r, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out[: max(1, int(limit))]


def _record_benchmark_history_row(row: dict[str, Any]) -> tuple[bool, str]:
    global LAST_BENCHMARK_HISTORY
    rr = dict(row or {})
    LAST_BENCHMARK_HISTORY = ([rr] + list(LAST_BENCHMARK_HISTORY or []))[:100]
    return _append_benchmark_history_row(rr)


def _benchmark_compare_rows(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    ls = dict((left or {}).get("summary") or {})
    rs = dict((right or {}).get("summary") or {})
    def _f(x: Any) -> float:
        try:
            return float(x or 0.0)
        except Exception:
            return 0.0
    return {
        "left_run_id": str((left or {}).get("run_id") or ls.get("run_id") or ""),
        "right_run_id": str((right or {}).get("run_id") or rs.get("run_id") or ""),
        "left": ls,
        "right": rs,
        "delta": {
            "accuracy": round(_f(rs.get("accuracy")) - _f(ls.get("accuracy")), 4),
            "pass": int(rs.get("pass") or 0) - int(ls.get("pass") or 0),
            "fail": int(rs.get("fail") or 0) - int(ls.get("fail") or 0),
            "latency_mean_ms": round(_f(rs.get("latency_mean_ms")) - _f(ls.get("latency_mean_ms")), 3),
            "tokens_total_est": int(rs.get("tokens_total_est") or 0) - int(ls.get("tokens_total_est") or 0),
        },
    }

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
    coordinator = _get_coordinator()

    try:
        base = inspect_state(
            root=MEMORY_ROOT,
            session_id=coordinator.session_id,
            as_of=as_of,
            limit_beads=300,
            limit_associations=300,
            limit_flushes=20,
            limit_merge_proposals=40,
        )
    except Exception as exc:
        base = {
            "ok": False,
            "error": str(exc),
            "memory": {"beads": [], "associations": [], "rolling_window": []},
            "claims": {"slots": [], "counts": {"active": 0, "conflict": 0, "retracted": 0, "historical": 0, "other": 0}, "as_of": as_of or None},
            "entities": {"rows": [], "counts": {"total": 0, "active": 0, "merged": 0, "other": 0}, "merge_proposals": []},
            "runtime": {"queue": {}, "queue_breakdown": [], "semantic_backend": {}, "recent_flushes": []},
            "stats": {"total_beads": 0, "total_associations": 0, "rolling_window_size": 0, "claim_slot_count": 0, "entity_count": 0},
        }

    runtime = dict(base.get("runtime") or {})
    recent_flushes = list(runtime.get("recent_flushes") or [])
    runtime["flush_history"] = recent_flushes
    runtime["last_flush"] = dict(recent_flushes[0] or {}) if recent_flushes else {}
    runtime.setdefault("myelination", {})

    session_payload = {
        "session_id": coordinator.session_id,
        "token_usage": coordinator.token_usage,
        "context_budget": coordinator.context_budget,
    }

    state_payload = {
        "session": session_payload,
        "memory": dict(base.get("memory") or {}),
        "claims": dict(base.get("claims") or {}),
        "entities": dict(base.get("entities") or {}),
        "runtime": runtime,
        "last_turn": dict(LAST_TURN_DIAGNOSTICS or {}),
        "benchmark": {
            "last_summary": dict(LAST_BENCHMARK_SUMMARY or {}),
            "has_last_report": bool(LAST_BENCHMARK_REPORT),
            "history": _read_benchmark_history(limit=10),
        },
    }

    memory = dict(state_payload.get("memory") or {})
    claims = dict(state_payload.get("claims") or {})
    entities = dict(state_payload.get("entities") or {})
    stats = dict(base.get("stats") or {})

    state_payload.update(
        {
            "beads": list(memory.get("beads") or []),
            "associations": list(memory.get("associations") or []),
            "rolling_window": list(memory.get("rolling_window") or []),
            "claim_state": list(claims.get("slots") or []),
            "stats": {
                "total_beads": int(stats.get("total_beads") or len(list(memory.get("beads") or []))),
                "total_associations": int(stats.get("total_associations") or len(list(memory.get("associations") or []))),
                "rolling_window_size": int(stats.get("rolling_window_size") or len(list(memory.get("rolling_window") or []))),
                "claim_slot_count": int(stats.get("claim_slot_count") or len(list(claims.get("slots") or []))),
                "entity_count": int(stats.get("entity_count") or len(list(entities.get("rows") or []))),
                "session_id": coordinator.session_id,
                "token_usage": coordinator.token_usage,
                "context_budget": coordinator.context_budget,
            },
        }
    )

    return state_payload


def _build_preload_turns_file_from_demo(*, max_turns: int = 200) -> str:
    """Create a temporary JSONL preload file from public turn surfaces."""
    out_rows: list[dict[str, Any]] = []
    cursor: str | None = None
    target = max(1, int(max_turns))

    while len(out_rows) < target:
        page = list_turn_summaries(root=MEMORY_ROOT, limit=min(200, target * 2), cursor=cursor)
        items = list(page.get("items") or [])
        if not items:
            break

        for rec in items:
            tid = str(rec.get("turn_id") or "").strip()
            sid = str(rec.get("session_id") or "").strip() or None
            if not tid:
                continue
            full = get_turn(turn_id=tid, root=MEMORY_ROOT, session_id=sid)
            if not isinstance(full, dict):
                full = rec
            uq = str(full.get("user_query") or "").strip()
            af = str(full.get("assistant_final") or "").strip()
            if not uq or not af:
                continue
            out_rows.append(
                {
                    "session_id": str(full.get("session_id") or sid or "demo"),
                    "turn_id": str(full.get("turn_id") or tid),
                    "user_query": uq[:500],
                    "assistant_final": af[:900],
                    "origin": "DEMO_PRELOAD",
                }
            )
            if len(out_rows) >= target:
                break

        cursor = str(page.get("next_cursor") or "").strip() or None
        if not cursor:
            break

    if not out_rows:
        return ""

    out_rows = out_rows[:target]
    fd, path = tempfile.mkstemp(prefix="demo-preload-", suffix=".jsonl")
    os.close(fd)
    out = Path(path)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out_rows), encoding="utf-8")
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
            flush_payload = {
                "flushed_session": flush_result.get("flushed_session"),
                "new_session": flush_result.get("new_session"),
                "flush_ok": bool((flush_result.get("flush_result") or {}).get("ok", False)),
                "rolling_window_beads": int(len(((flush_result.get("flush_result") or {}).get("rolling_window") or {}).get("records") or [])),
            }
            _record_flush_event(flush_payload, trigger="auto_threshold")
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


@app.get("/v1/memory/inspect/state")
async def inspect_state_http(request: Request):
    as_of = str(request.query_params.get("as_of") or "").strip() or None
    session_id = str(request.query_params.get("session_id") or "").strip() or _get_coordinator().session_id
    limit_beads = int(request.query_params.get("limit_beads") or 300)
    limit_associations = int(request.query_params.get("limit_associations") or 300)
    limit_flushes = int(request.query_params.get("limit_flushes") or 20)
    limit_merge_proposals = int(request.query_params.get("limit_merge_proposals") or 40)
    out = inspect_state(
        root=MEMORY_ROOT,
        session_id=session_id,
        as_of=as_of,
        limit_beads=max(1, limit_beads),
        limit_associations=max(1, limit_associations),
        limit_flushes=max(1, limit_flushes),
        limit_merge_proposals=max(1, limit_merge_proposals),
    )
    return JSONResponse(dict(out or {}))


@app.get("/v1/memory/inspect/beads/{bead_id}")
async def inspect_bead_http(bead_id: str):
    bead = inspect_bead(root=MEMORY_ROOT, bead_id=str(bead_id))
    if not isinstance(bead, dict):
        return JSONResponse({"ok": False, "error": "bead_not_found", "bead_id": str(bead_id)}, status_code=404)
    return JSONResponse({"ok": True, "bead": bead})


@app.get("/v1/memory/inspect/beads/{bead_id}/hydrate")
async def inspect_bead_hydrate_http(bead_id: str, include_tools: bool = False, before: int = 0, after: int = 0):
    out = inspect_bead_hydration(
        root=MEMORY_ROOT,
        bead_id=str(bead_id),
        include_tools=bool(include_tools),
        before=max(0, int(before)),
        after=max(0, int(after)),
    )
    return JSONResponse(dict(out or {}))


@app.get("/v1/memory/inspect/claim-slots/{subject}/{slot}")
async def inspect_claim_slot_http(subject: str, slot: str, as_of: str | None = None):
    out = inspect_claim_slot(root=MEMORY_ROOT, subject=subject, slot=slot, as_of=(str(as_of or "").strip() or None))
    return JSONResponse(dict(out or {}))


@app.get("/v1/memory/inspect/turns")
async def inspect_turns_http(session_id: str | None = None, limit: int = 200, cursor: str | None = None):
    out = list_turn_summaries(
        root=MEMORY_ROOT,
        session_id=(str(session_id or "").strip() or None),
        limit=max(1, int(limit)),
        cursor=(str(cursor or "").strip() or None),
    )
    return JSONResponse(dict(out or {}))


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


@app.get("/api/demo/entities")
async def demo_entities_endpoint():
    state = get_memory_state()
    return JSONResponse(
        {
            "ok": True,
            "entities": dict(state.get("entities") or {}),
            "session": dict(state.get("session") or {}),
        }
    )


@app.post("/api/demo/entities/merge/suggest")
async def demo_entities_merge_suggest_endpoint(request: Request):
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    min_score = float((body or {}).get("min_score") or 0.86)
    max_pairs = int((body or {}).get("max_pairs") or 40)
    source = str((body or {}).get("source") or "demo").strip() or "demo"
    try:
        out = suggest_entity_merge_proposals(MEMORY_ROOT, min_score=min_score, max_pairs=max_pairs, source=source)
        return JSONResponse({"ok": bool(out.get("ok", True)), **dict(out or {})})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@app.post("/api/demo/entities/merge/decide")
async def demo_entities_merge_decide_endpoint(request: Request):
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    proposal_id = str((body or {}).get("proposal_id") or "").strip()
    decision = str((body or {}).get("decision") or "").strip().lower()
    keep_entity_id = str((body or {}).get("keep_entity_id") or "").strip() or None
    reviewer = str((body or {}).get("reviewer") or "demo").strip() or "demo"
    notes = str((body or {}).get("notes") or "").strip()
    apply_merge = bool((body or {}).get("apply", True))

    if not proposal_id:
        return JSONResponse({"ok": False, "error": "missing_proposal_id"}, status_code=400)
    if decision not in {"accept", "reject"}:
        return JSONResponse({"ok": False, "error": "invalid_decision"}, status_code=400)

    try:
        out = decide_entity_merge_proposal(
            MEMORY_ROOT,
            proposal_id=proposal_id,
            decision=decision,
            reviewer=reviewer,
            notes=notes,
            apply=apply_merge,
            keep_entity_id=keep_entity_id,
        )
        status = 200 if bool(out.get("ok")) else 400
        return JSONResponse({"ok": bool(out.get("ok")), **dict(out or {})}, status_code=status)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@app.get("/api/demo/benchmark/last")
async def demo_benchmark_last_endpoint():
    history = _read_benchmark_history(limit=10)
    latest_compare = None
    if len(history) >= 2:
        latest_compare = _benchmark_compare_rows(history[1], history[0])
    return JSONResponse(
        {
            "ok": bool(LAST_BENCHMARK_REPORT),
            "summary": dict(LAST_BENCHMARK_SUMMARY or {}),
            "report": dict(LAST_BENCHMARK_REPORT or {}),
            "history": history,
            "latest_compare": latest_compare,
        }
    )


@app.get("/api/demo/benchmark/history")
async def demo_benchmark_history_endpoint(request: Request):
    limit = int(request.query_params.get("limit") or 20)
    rows = _read_benchmark_history(limit=max(1, min(200, limit)))
    return JSONResponse({"ok": True, "history": rows})


@app.get("/api/demo/benchmark/compare/{left_run_id}/{right_run_id}")
async def demo_benchmark_compare_endpoint(left_run_id: str, right_run_id: str):
    rows = _read_benchmark_history(limit=400)
    by_id = {str(r.get("run_id") or ""): r for r in rows if str(r.get("run_id") or "")}
    left = dict(by_id.get(str(left_run_id)) or {})
    right = dict(by_id.get(str(right_run_id)) or {})
    if not left or not right:
        return JSONResponse({"ok": False, "error": "run_id_not_found"}, status_code=404)
    return JSONResponse({"ok": True, "compare": _benchmark_compare_rows(left, right)})


@app.get("/api/demo/bead/{bead_id}")
async def demo_bead_endpoint(bead_id: str):
    return await get_bead(bead_id)


@app.get("/api/demo/bead/{bead_id}/hydrate")
async def demo_bead_hydrate_endpoint(bead_id: str):
    try:
        out = inspect_bead_hydration(root=MEMORY_ROOT, bead_id=str(bead_id), include_tools=False, before=0, after=0)
        return JSONResponse(dict(out or {}))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc), "bead_id": bead_id}, status_code=500)


@app.get("/api/demo/claim-slot/{subject}/{slot}")
async def demo_claim_slot_endpoint(subject: str, slot: str, request: Request):
    try:
        as_of = str(request.query_params.get("as_of") or "").strip() or None
        out = inspect_claim_slot(root=MEMORY_ROOT, subject=subject, slot=slot, as_of=as_of)
        return JSONResponse(dict(out or {}))
    except Exception as exc:
        key = f"{str(subject).strip()}:{str(slot).strip()}"
        return JSONResponse({"ok": False, "error": str(exc), "slot_key": key, "as_of": as_of}, status_code=500)


@app.post("/api/flush")
async def flush_endpoint():
    """Manual session flush: archive, compress, rebuild rolling window."""
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
    _record_flush_event(payload, trigger="manual")
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
    run_id = f"bench-{uuid.uuid4().hex[:10]}"
    started_at = _utc_now_iso()
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
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": _utc_now_iso(),
            "cases": int(totals.get("cases") or 0),
            "pass": int(totals.get("pass") or 0),
            "fail": int(totals.get("fail") or 0),
            "accuracy": float(totals.get("accuracy") or 0.0),
            "latency_mean_ms": float(((report.get("latency_ms") or {}).get("mean") or 0.0)),
            "tokens_total_est": int(((report.get("token_usage") or {}).get("total_tokens_est") or 0)),
            "backend_modes": list(meta.get("benchmark_backend_modes") or []),
            "preload_turn_count": int(meta.get("preload_turn_count") or 0),
            "semantic_mode": str(meta.get("semantic_mode") or ""),
            "root_mode": root_mode_effective,
            "isolated_root": benchmark_temp_root,
            "isolated_run": True,
            "warnings": snapshot_copy_warnings,
        }

        mc = dict(report.get("myelination_comparison") or {})
        if mc:
            rows = list(mc.get("cases") or [])
            improved = sum(1 for r in rows if (not bool(r.get("baseline_pass"))) and bool(r.get("enabled_pass")))
            regressed = sum(1 for r in rows if bool(r.get("baseline_pass")) and (not bool(r.get("enabled_pass"))))
            summary["myelination_compare"] = {
                "accuracy_delta": float(mc.get("accuracy_delta") or 0.0),
                "improved_cases": int(improved),
                "regressed_cases": int(regressed),
                "changed_cases": int(sum(1 for r in rows if bool(r.get("pass_changed")))),
            }

        history_row = {
            "run_id": run_id,
            "created_at": summary.get("finished_at"),
            "summary": dict(summary),
            "metadata": {
                "subset": subset,
                "semantic_mode": semantic_mode,
                "vector_backend": vector_backend,
                "myelination": myelination_mode,
                "root_mode": root_mode_effective,
            },
            "per_bucket": dict(report.get("per_bucket") or {}),
            "myelination_comparison": dict(report.get("myelination_comparison") or {}),
        }
        wrote_hist, hist_err = _record_benchmark_history_row(history_row)
        if not wrote_hist and hist_err:
            summary["warnings"] = list(summary.get("warnings") or []) + [f"benchmark_history_write_failed:{hist_err}"]

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
    bead = inspect_bead(root=MEMORY_ROOT, bead_id=str(bead_id))
    if not isinstance(bead, dict):
        return JSONResponse({"error": "Bead not found"}, status_code=404)
    return JSONResponse(bead)


# ── Seed (CLI-only, for quick demos) ──────────────────────────────────

def _seed_demo_history():
    seed_turns = [
        {
            "turn_id": "seed-001",
            "user": "Should we use MySQL or PostgreSQL for our JSON-heavy service?",
            "assistant": "Decision: choose PostgreSQL. Benchmark notes show ~2x better latency for JSONB-heavy workloads and better indexing flexibility.",
        },
        {
            "turn_id": "seed-002",
            "user": "What lesson did we learn from database selection?",
            "assistant": "Lesson: benchmark representative workload before infra decisions. Synthetic-only benchmarks previously misled us.",
        },
        {
            "turn_id": "seed-003",
            "user": "What evidence supports PostgreSQL?",
            "assistant": "Evidence: pgbench + sysbench on representative JSON workload showed PostgreSQL ~2x better median and p95 latency than MySQL.",
        },
        {
            "turn_id": "seed-004",
            "user": "What project goal is pending this quarter?",
            "assistant": "Goal: migrate authentication to OAuth2 by end of Q2, including Google and GitHub providers.",
        },
        {
            "turn_id": "seed-005",
            "user": "Why did we adopt FastAPI for HTTP?",
            "assistant": "Decision: adopted FastAPI for async-first I/O, OpenAPI support, and native validation; Flask lacked async and Django was heavier than needed.",
        },
    ]

    for i, row in enumerate(seed_turns, start=1):
        process_turn_finalized(
            root=MEMORY_ROOT,
            session_id="seed-history",
            turn_id=str(row["turn_id"]),
            transaction_id=f"seed-tx-{i:03d}",
            trace_id=f"seed-tr-{i:03d}",
            user_query=str(row["user"]),
            assistant_final=str(row["assistant"]),
            origin="DEMO_SEED",
            metadata={"source": "demo_seed", "seed": True},
        )
    print("  Seeded 5 sample turns via canonical process_turn_finalized boundary")


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
