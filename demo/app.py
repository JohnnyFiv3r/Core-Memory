"""Core Memory Live Demo — Web UI

A single-file FastAPI app that demonstrates the full memory lifecycle:
  - Chat with an LLM that has memory continuity + tools
  - Watch beads + associations appear in real time (per-turn pipeline)
  - Flush session: archive, compress, rebuild rolling window
  - Auto-flush when context token budget hits 80%
  - Ask "why" questions and trace answers back to specific bead IDs

Usage:
    # 1. Put your API key in .env at the repo root
    # 2. Run:
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
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

# Load .env from repo root before anything else
from dotenv import load_dotenv

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

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

# Add parent to path so we can import core_memory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core_memory.persistence.store import MemoryStore
from core_memory.write_pipeline.continuity_injection import load_continuity_injection
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

app = FastAPI(title="Core Memory Demo")


# ── Helpers ───────────────────────────────────────────────────────────

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
        return continuity_prompt(root=MEMORY_ROOT)

    return agent


def get_memory_state() -> dict:
    """Read current beads, associations, and rolling window for the inspector."""
    assert COORDINATOR is not None

    store = MemoryStore(root=MEMORY_ROOT)
    index_path = store.beads_dir / "index.json"
    if not index_path.exists():
        return {
            "beads": [], "associations": [], "rolling_window": [],
            "stats": {
                "total_beads": 0, "total_associations": 0, "rolling_window_size": 0,
                "session_id": COORDINATOR.session_id,
                "token_usage": COORDINATOR.token_usage,
                "context_budget": COORDINATOR.context_budget,
            },
        }

    index = store._read_json(index_path)
    beads_map = index.get("beads") or {}

    beads = []
    for b in sorted(beads_map.values(), key=lambda x: x.get("created_at", ""), reverse=True):
        beads.append({
            "id": b.get("id", ""),
            "type": b.get("type", ""),
            "title": b.get("title", ""),
            "summary": b.get("summary", []),
            "status": b.get("status", "candidate"),
            "session_id": b.get("session_id", ""),
            "source_turn_ids": b.get("source_turn_ids", []),
            "created_at": b.get("created_at", ""),
            "detail": b.get("detail", ""),
        })

    associations = []
    for a in (index.get("associations") or []):
        associations.append({
            "id": a.get("id", ""),
            "source_bead": a.get("source_bead", ""),
            "target_bead": a.get("target_bead", ""),
            "relationship": a.get("relationship", ""),
            "explanation": a.get("explanation", ""),
            "confidence": a.get("confidence", 0),
        })

    try:
        ctx = load_continuity_injection(MEMORY_ROOT)
        rolling = ctx.get("records") or []
    except Exception:
        rolling = []

    return {
        "beads": beads,
        "associations": associations,
        "rolling_window": [{"title": r.get("title", ""), "type": r.get("type", "")} for r in rolling],
        "stats": {
            "total_beads": len(beads),
            "total_associations": len(associations),
            "rolling_window_size": len(rolling),
            "session_id": COORDINATOR.session_id,
            "token_usage": COORDINATOR.token_usage,
            "context_budget": COORDINATOR.context_budget,
        },
    }


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


# ── API Routes ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.post("/api/chat")
async def chat(request: Request):
    assert AGENT is not None and COORDINATOR is not None

    body = await request.json()
    user_message = body.get("message", "").strip()
    if not user_message:
        return JSONResponse({"error": "Empty message"}, status_code=400)

    turn_id = COORDINATOR.next_turn_id()
    auto_flushed = False

    try:
        result = await run_with_memory(
            AGENT,
            user_message,
            root=MEMORY_ROOT,
            session_id=COORDINATOR.session_id,
            turn_id=turn_id,
        )
        assistant_text = result.output if hasattr(result, "output") else str(result.data)
    except Exception as exc:
        assistant_text = f"Error: {exc}"

    COORDINATOR.record_turn_tokens(user_message, assistant_text)

    # Auto-flush if context budget threshold exceeded
    if COORDINATOR.should_auto_flush():
        try:
            flush_result = COORDINATOR.do_flush()
            auto_flushed = True
            logger.info("auto-flush triggered: %s", flush_result)
        except Exception as exc:
            logger.warning("auto-flush failed: %s", exc)

    return JSONResponse({
        "response": assistant_text,
        "turn_id": turn_id,
        "session_id": COORDINATOR.session_id,
        "auto_flushed": auto_flushed,
    })


@app.get("/api/memory")
async def memory_state():
    return JSONResponse(get_memory_state())


@app.post("/api/flush")
async def flush_endpoint():
    """Manual session flush: archive, compress, rebuild rolling window."""
    assert COORDINATOR is not None

    try:
        result = COORDINATOR.do_flush()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    return JSONResponse({
        "flushed_session": result["flushed_session"],
        "new_session": result["new_session"],
        "flush_ok": result.get("flush_result", {}).get("ok", False),
        "rolling_window_beads": int(len((result.get("flush_result", {}).get("rolling_window") or {}).get("records") or [])),
    })


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
    limit_raw = (body or {}).get("limit")
    limit = int(limit_raw) if isinstance(limit_raw, int) and limit_raw > 0 else None

    preload_from_demo = bool((body or {}).get("preload_from_demo", False))
    preload_turns_max = int((body or {}).get("preload_turns_max") or 200)

    preload_file = ""
    try:
        if preload_from_demo:
            preload_file = _build_preload_turns_file_from_demo(max_turns=preload_turns_max)

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
            benchmark_root=MEMORY_ROOT,
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
            "visual_mode": True,
        }
        return JSONResponse({"ok": True, "summary": summary, "report": report})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
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
