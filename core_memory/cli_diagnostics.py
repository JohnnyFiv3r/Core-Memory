from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .persistence.store import MemoryStore
from .runtime.engine import process_turn_finalized, process_flush
from .retrieval.tools.memory import execute as memory_execute_tool


def canonical_health_report(root: str, write_path: str | None = None) -> dict:
    checks = {}
    with tempfile.TemporaryDirectory() as td:
        t1 = process_turn_finalized(
            root=td,
            session_id="health",
            turn_id="t1",
            user_query="remember canonical decision",
            assistant_final="Decision: keep canonical path and stable retrieval.",
        )
        f1 = process_flush(root=td, session_id="health", promote=True, token_budget=800, max_beads=10, source="canonical_health")
        f2 = process_flush(root=td, session_id="health", promote=True, token_budget=800, max_beads=10, source="canonical_health")

        phase_trace = ((f1.get("result") or {}).get("phase_trace") or [])
        checks["turn_path"] = bool(t1.get("ok"))
        checks["flush_once_per_cycle"] = bool(
            f2.get("skipped")
            and str(f2.get("reason") or "") in {"already_flushed_for_latest_turn", "already_flushed_for_latest_done_turn"}
        )
        checks["rolling_window_maintenance"] = bool("rolling_window_write" in phase_trace)
        checks["archive_ergonomics"] = bool("archive_compact_session" in phase_trace and "archive_compact_historical" in phase_trace)

        req = {"raw_query": "canonical decision", "intent": "remember", "k": 5}
        ret = memory_execute_tool(req, root=td, explain=True)
        checks["retrieval_path"] = bool((ret.get("ok") is True) or (ret.get("results") is not None) or (ret.get("items") is not None))

    out = {
        "ok": True,
        "schema": "openclaw.memory.canonical_health_report.v1",
        "root": str(root),
        "checks": checks,
        "all_green": all(bool(v) for v in checks.values()),
    }
    if write_path:
        p = Path(write_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2), encoding="utf-8")
        out["written"] = str(p)
    return out


def doctor_report(root: str) -> dict:
    root_p = Path(root)
    beads_dir = root_p / ".beads"
    idx_file = beads_dir / "index.json"
    from core_memory.persistence.rolling_record_store import read_rolling_records

    checks: list[dict] = []

    exists = beads_dir.exists() and beads_dir.is_dir()
    writable = os.access(beads_dir, os.W_OK) if exists else False
    checks.append(
        {
            "name": ".beads directory exists and writable",
            "pass": bool(exists and writable),
            "detail": {"path": str(beads_dir), "exists": bool(exists), "writable": bool(writable)},
        }
    )

    index_ok = False
    index = {}
    index_error = ""
    try:
        index = json.loads(idx_file.read_text(encoding="utf-8"))
        index_ok = True
    except Exception as e:
        index_error = str(e)
    checks.append({"name": "index.json exists and valid JSON", "pass": bool(index_ok), "detail": {"path": str(idx_file), "error": index_error or None}})

    beads = (index.get("beads") or {}) if isinstance(index, dict) else {}
    by_status: dict[str, int] = {}
    for b in beads.values():
        s = str((b or {}).get("status") or "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    checks.append({"name": "bead count", "pass": bool(index_ok), "detail": {"total": int(len(beads)), "by_status": by_status}})

    session_count = len(list(beads_dir.glob("session-*.jsonl"))) if exists else 0
    checks.append({"name": "session file count", "pass": bool(exists), "detail": {"count": int(session_count)}})

    checkpoints_file = beads_dir / "events" / "flush-checkpoints.jsonl"
    flush_cycle_seen = bool(checkpoints_file.exists() and checkpoints_file.stat().st_size > 0)
    rr = read_rolling_records(root)
    rolling_exists = bool(rr.get("records"))
    checks.append(
        {
            "name": "rolling-window records present (required after first flush cycle)",
            "pass": bool(rolling_exists or not flush_cycle_seen),
            "detail": {
                "path": str(root_p),
                "exists": rolling_exists,
                "required_after_first_flush": True,
                "flush_cycle_seen": flush_cycle_seen,
            },
        }
    )

    orphan_count = 0
    if index_ok:
        bead_ids = set(str(k) for k in beads.keys())
        for a in (index.get("associations") or []):
            src = str((a or {}).get("source_bead") or (a or {}).get("source_bead_id") or "")
            dst = str((a or {}).get("target_bead") or (a or {}).get("target_bead_id") or "")
            if (src and src not in bead_ids) or (dst and dst not in bead_ids):
                orphan_count += 1
    checks.append({"name": "no orphaned association references", "pass": bool(index_ok and orphan_count == 0), "detail": {"orphaned_associations": int(orphan_count)}})

    ok = all(bool(c.get("pass")) for c in checks)
    return {
        "ok": bool(ok),
        "schema": "core_memory.doctor.v1",
        "root": str(root_p),
        "checks": checks,
    }


def simple_recall_fallback(memory: MemoryStore, query_text: str, limit: int = 8) -> dict:
    """Best-effort lexical fallback for plug-and-play recall search."""
    q = str(query_text or "").strip().lower()
    if not q:
        return {"ok": True, "results": []}

    tokens = [t for t in q.split() if t]
    candidates = memory.query(limit=500)
    out = []
    for b in candidates:
        title = str((b or {}).get("title") or "")
        summary = " ".join(str(x) for x in ((b or {}).get("summary") or []))
        detail = str((b or {}).get("detail") or "")
        tags = " ".join(str(x) for x in ((b or {}).get("tags") or []))
        hay = f"{title} {summary} {detail} {tags}".lower()
        if q in hay or any(tok in hay for tok in tokens):
            score = 1.0 if q in hay else 0.8
            out.append(
                {
                    "bead_id": str((b or {}).get("id") or ""),
                    "type": str((b or {}).get("type") or ""),
                    "title": title,
                    "summary": (b or {}).get("summary") or [],
                    "score": score,
                    "source": "cli_simple_fallback",
                }
            )
    out = sorted(out, key=lambda r: float(r.get("score") or 0.0), reverse=True)[: max(1, int(limit or 8))]
    return {"ok": True, "results": out}
