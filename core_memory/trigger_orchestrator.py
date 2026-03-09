from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sidecar import mark_memory_pass, try_claim_memory_pass
from .sidecar_hook import maybe_emit_finalize_memory_event
from .sidecar_worker import SidecarPolicy, process_memory_event
from .store import MemoryStore
from .write_pipeline.orchestrate import run_consolidate_pipeline


def run_turn_finalize_pipeline(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    transaction_id: str,
    trace_id: str,
    user_query: str,
    assistant_final: str,
    trace_depth: int = 0,
    origin: str = "USER_TURN",
    tools_trace: list[dict] | None = None,
    mesh_trace: list[dict] | None = None,
    window_turn_ids: list[str] | None = None,
    window_bead_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    policy: SidecarPolicy | None = None,
) -> dict[str, Any]:
    """Canonical in-process turn-finalized trigger pipeline (V2-P2 Step 1).

    This mirrors current finalize+process behavior while establishing a single
    orchestration boundary for per-turn trigger execution.
    """
    emitted = maybe_emit_finalize_memory_event(
        root,
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id,
        user_query=user_query,
        assistant_final=assistant_final,
        trace_depth=trace_depth,
        origin=origin,
        tools_trace=tools_trace,
        mesh_trace=mesh_trace,
        window_turn_ids=window_turn_ids,
        window_bead_ids=window_bead_ids,
        metadata=metadata,
    )

    if not emitted.get("emitted"):
        return {
            "ok": True,
            "mode": "turn",
            "emitted": emitted,
            "processed": 0,
            "failed": 0,
        }

    last_row = emitted.get("payload") if isinstance(emitted, dict) else None
    if not last_row:
        events_file = Path(root) / ".beads" / "events" / "memory-events.jsonl"
        if not events_file.exists():
            return {
                "ok": False,
                "mode": "turn",
                "emitted": emitted,
                "processed": 0,
                "failed": 1,
                "error": "events_file_missing_after_emit",
            }
        with open(events_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                env = row.get("envelope") or {}
                if env.get("session_id") == session_id and env.get("turn_id") == turn_id:
                    last_row = row

    if not last_row:
        return {
            "ok": False,
            "mode": "turn",
            "emitted": emitted,
            "processed": 0,
            "failed": 1,
            "error": "event_row_not_found",
        }

    claimed, state_after = try_claim_memory_pass(Path(root), session_id, turn_id)
    if not claimed:
        return {
            "ok": True,
            "mode": "turn",
            "emitted": emitted,
            "processed": 0,
            "failed": 0,
            "reason": "not_claimed",
        }

    try:
        delta = process_memory_event(root, last_row, policy=policy)
    except Exception as exc:  # noqa: BLE001
        mark_memory_pass(
            Path(root),
            session_id,
            turn_id,
            "failed",
            envelope_hash=(state_after or {}).get("envelope_hash", ""),
            reason="direct_turn_exception",
            error=str(exc),
        )
        return {
            "ok": False,
            "mode": "turn",
            "emitted": emitted,
            "processed": 0,
            "failed": 1,
            "error": str(exc),
        }

    kpi_logged = False
    kpi_error = None
    try:
        store = MemoryStore(root=root)
        env = (last_row.get("envelope") or {})
        md = env.get("metadata") or {}
        store.append_autonomy_kpi(
            run_id=f"auto-{session_id}-{turn_id}",
            repeat_failure=False,
            contradiction_resolved=(emitted.get("reason") == "turn_mutation"),
            contradiction_latency_turns=0,
            unjustified_flip=False,
            constraint_violation=bool(md.get("constraint_violation", False)),
            wrong_transfer=bool(md.get("wrong_transfer", False)),
            goal_carryover=bool((env.get("window_turn_ids") or []) or (env.get("window_bead_ids") or [])),
        )
        kpi_logged = True
    except Exception as exc:  # noqa: BLE001
        kpi_error = str(exc)

    return {
        "ok": True,
        "mode": "turn",
        "emitted": emitted,
        "processed": 1,
        "failed": 0,
        "delta": delta,
        "kpi_logged": kpi_logged,
        "kpi_error": kpi_error,
    }


def _flush_checkpoint_file(root: str) -> Path:
    p = Path(root) / ".beads" / "events" / "flush-checkpoints.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _flush_ckpt(root: str, payload: dict[str, Any]) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    p = _flush_checkpoint_file(root)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_flush_pipeline(
    *,
    root: str,
    session_id: str,
    promote: bool,
    token_budget: int,
    max_beads: int,
    source: str = "flush_hook",
    flush_tx_id: str | None = None,
) -> dict[str, Any]:
    """Canonical flush trigger pipeline entrypoint (V2-P2 Step 2)."""
    tx = str(flush_tx_id or f"flush-{session_id}-{int(datetime.now(timezone.utc).timestamp())}")
    _flush_ckpt(root, {"flush_tx_id": tx, "session_id": session_id, "stage": "start", "source": source, "status": "pending"})

    # Stage: enrichment barrier (placeholder marker in step 2; hard-enforced in later step)
    _flush_ckpt(root, {"flush_tx_id": tx, "session_id": session_id, "stage": "enrichment_ready", "status": "done"})

    out = run_consolidate_pipeline(
        session_id=session_id,
        promote=bool(promote),
        token_budget=int(token_budget),
        max_beads=int(max_beads),
    )
    if not out.get("ok"):
        _flush_ckpt(root, {"flush_tx_id": tx, "session_id": session_id, "stage": "failed", "status": "failed", "error": out.get("error")})
        return {"ok": False, "flush_tx_id": tx, "error": out.get("error"), "result": out}

    _flush_ckpt(root, {"flush_tx_id": tx, "session_id": session_id, "stage": "archive_persisted", "status": "done"})
    _flush_ckpt(root, {"flush_tx_id": tx, "session_id": session_id, "stage": "rolling_written", "status": "done"})
    _flush_ckpt(root, {"flush_tx_id": tx, "session_id": session_id, "stage": "committed", "status": "committed"})

    return {"ok": True, "flush_tx_id": tx, "result": out}
