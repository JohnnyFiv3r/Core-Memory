from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.retrieval.agent import recall as core_recall
from core_memory.runtime.engine import process_turn_finalized
from core_memory.runtime.queue.jobs import run_async_jobs

from benchmarks.locomo.scoring import aggregate_case_scores

from .loader import LongMemEvalAdapter, LongMemEvalLoaderError


def _repo_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=8", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def run_adapter_smoke(*, corpus: Path | str, limit: int = 1) -> dict[str, Any]:
    adapter = LongMemEvalAdapter()
    conversations = adapter.load_conversations(corpus_path=Path(corpus), limit=max(1, int(limit)))
    turn_count = sum(len(c.turns) for c in conversations)
    qa_count = sum(len(c.qa_cases) for c in conversations)
    question_types = sorted({
        str(c.metadata.get("question_type") or "unknown")
        for c in conversations
    })

    return {
        "schema_version": "longmemeval_adapter_smoke.v1",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _repo_sha(),
        "adapter": adapter.name,
        "status": "completed",
        "config": {
            "limit": max(1, int(limit)),
        },
        "summary": {
            "conversation_count": len(conversations),
            "turn_count": turn_count,
            "qa_count": qa_count,
            "question_types": question_types,
        },
        "sample": [
            {
                "conversation_id": c.conversation_id,
                "question_id": str(c.metadata.get("question_id") or ""),
                "question_type": str(c.metadata.get("question_type") or ""),
                "turn_count": len(c.turns),
                "qa_count": len(c.qa_cases),
                "answer_session_count": len(c.metadata.get("answer_session_ids") or []),
            }
            for c in conversations[:3]
        ],
        "leaderboard_claim": False,
    }


def _read_index(root: str) -> dict[str, Any]:
    path = Path(root) / ".beads" / "index.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _bead_id_for_turn(root: str, *, session_id: str, turn_id: str) -> str:
    idx = _read_index(root)
    beads = dict((idx.get("beads") or {})) if isinstance(idx, dict) else {}
    hits: list[dict[str, Any]] = []
    for bead in beads.values():
        if not isinstance(bead, dict):
            continue
        if str(bead.get("session_id") or "") != session_id:
            continue
        if turn_id in [str(x) for x in list(bead.get("source_turn_ids") or [])]:
            hits.append(bead)
    if not hits:
        return ""
    hits.sort(key=lambda b: str(b.get("created_at") or ""), reverse=True)
    return str(hits[0].get("id") or "")


def _crawler_updates(*, turn_id: str, content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    title = (text.splitlines()[0] if text else "LongMemEval turn")[:160]
    return {
        "beads_create": [
            {
                "type": "context",
                "title": title or "LongMemEval turn",
                "summary": [text[:240]],
                "because": [],
                "source_turn_ids": [turn_id],
                "entities": ["LongMemEval"],
                "topics": ["longmemeval"],
                "retrieval_eligible": True,
                "retrieval_title": title or text[:160] or "LongMemEval turn",
                "retrieval_facts": [text[:500]],
                "tags": ["longmemeval_replay", "benchmark_preload", "crawler_reviewed"],
            }
        ]
    }


def _ingest_conversation(root: str, conversation: Any) -> dict[str, str]:
    bead_to_session: dict[str, str] = {}
    for turn in conversation.turns:
        turn_session_id = str(turn.metadata.get("session_id") or conversation.session_id)
        process_turn_finalized(
            root=root,
            session_id=conversation.session_id,
            turn_id=turn.turn_id,
            transaction_id=f"tx-{turn.turn_id}",
            trace_id=f"tr-{turn.turn_id}",
            turns=[{"speaker": turn.speaker, "role": turn.role, "content": turn.content}],
            metadata={"crawler_updates": _crawler_updates(turn_id=turn.turn_id, content=turn.content), "replay_source": "longmemeval"},
            tools_trace=[],
            mesh_trace=[],
            origin="LONGMEMEVAL_BENCHMARK",
        )
        bead_id = _bead_id_for_turn(root, session_id=conversation.session_id, turn_id=turn.turn_id)
        if bead_id:
            bead_to_session[bead_id] = turn_session_id
    return bead_to_session


def _evidence_items(result: Any) -> list[Any]:
    try:
        return list(result.evidence or [])
    except Exception:
        payload = result.to_dict() if hasattr(result, "to_dict") else {}
        return list(payload.get("evidence") or [])


def _item_bead_id(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("bead_id") or "").strip()
    return str(getattr(item, "bead_id", "") or "").strip()


def _item_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("content_excerpt") or item.get("title") or "").strip()
    return str(getattr(item, "content_excerpt", "") or getattr(item, "title", "") or "").strip()


def run_evaluation_smoke(*, corpus: Path | str, limit: int = 1, k: int = 5) -> dict[str, Any]:
    adapter = LongMemEvalAdapter()
    conversations = adapter.load_conversations(corpus_path=Path(corpus), limit=max(1, int(limit)))
    t0 = time.perf_counter()
    all_cases: list[dict[str, Any]] = []
    conversation_rows: list[dict[str, Any]] = []

    for conversation in conversations:
        with tempfile.TemporaryDirectory(prefix="cm-longmemeval-eval-") as td:
            bead_to_session = _ingest_conversation(td, conversation)
            run_async_jobs(root=td, run_semantic=True, max_compaction=50, max_side_effects=50)
            case_rows: list[dict[str, Any]] = []
            for qa in conversation.qa_cases:
                result = core_recall(
                    qa.question,
                    effort="high",
                    root=td,
                    k=k,
                    explain=True,
                    include_raw=True,
                )
                evidence = _evidence_items(result)
                retrieved_sessions: list[str] = []
                prediction = ""
                for item in evidence:
                    bead_id = _item_bead_id(item)
                    sid = bead_to_session.get(bead_id, "")
                    if sid and sid not in retrieved_sessions:
                        retrieved_sessions.append(sid)
                    if not prediction:
                        prediction = _item_text(item)
                evidence_recall = adapter.score_evidence(qa=qa, retrieved_ids=retrieved_sessions, k=k)
                answer_f1 = adapter.score_answer(qa=qa, prediction=prediction)
                row = {
                    "qa_id": qa.qa_id,
                    "question_type": str(qa.category or ""),
                    "category": str(qa.category or ""),
                    "excluded": False,
                    "retrieved_session_ids": retrieved_sessions[:k],
                    "retrieved_dia_ids": retrieved_sessions[:k],
                    "retrieved_bead_count": len(evidence),
                    "evidence_recall": evidence_recall,
                    "answer_f1": round(answer_f1, 4),
                    "prediction_snippet": prediction[:200],
                    "bucket_labels": list(qa.bucket_labels),
                }
                case_rows.append(row)
                all_cases.append(row)
            conversation_rows.append({
                "conversation_id": conversation.conversation_id,
                "session_id": conversation.session_id,
                "turn_count": len(conversation.turns),
                "qa_count": len(case_rows),
                "bead_session_map_size": len(bead_to_session),
                "cases": case_rows,
            })

    return {
        "schema_version": "longmemeval_evaluation_smoke.v1",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _repo_sha(),
        "adapter": adapter.name,
        "status": "completed",
        "config": {
            "limit": max(1, int(limit)),
            "k": max(1, int(k)),
        },
        "summary": {
            "conversation_count": len(conversations),
            "turn_count": sum(len(c.turns) for c in conversations),
            "qa_count": sum(len(c.qa_cases) for c in conversations),
        },
        "aggregate": aggregate_case_scores(all_cases),
        "conversations": conversation_rows,
        "leaderboard_claim": False,
        "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Run a LongMemEval adapter load smoke")
    p.add_argument("--corpus", required=True, help="Path to a user-supplied LongMemEval JSON or JSONL corpus")
    p.add_argument("--limit", type=int, default=1, help="Maximum question instances to load")
    p.add_argument("--eval-smoke", action="store_true", help="Run bounded lifecycle evaluation smoke instead of load smoke")
    p.add_argument("--k", type=int, default=5, help="Retrieval k for --eval-smoke")
    p.add_argument("--out", default="")
    p.add_argument("--pretty", action="store_true")
    args = p.parse_args()

    try:
        if args.eval_smoke:
            report = run_evaluation_smoke(corpus=Path(args.corpus), limit=int(args.limit), k=int(args.k))
        else:
            report = run_adapter_smoke(corpus=Path(args.corpus), limit=int(args.limit))
    except LongMemEvalLoaderError as exc:
        report = {
            "schema_version": "longmemeval_adapter_smoke.v1",
            "run_at": datetime.now(timezone.utc).isoformat(),
            "git_sha": _repo_sha(),
            "adapter": "longmemeval",
            "status": "failed",
            "error": str(exc),
            "leaderboard_claim": False,
        }

    text = json.dumps(report, indent=2 if args.pretty else None)
    print(text)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
