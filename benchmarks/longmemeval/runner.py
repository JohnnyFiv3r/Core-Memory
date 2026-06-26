from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def main() -> int:
    p = argparse.ArgumentParser(description="Run a LongMemEval adapter load smoke")
    p.add_argument("--corpus", required=True, help="Path to a user-supplied LongMemEval JSON or JSONL corpus")
    p.add_argument("--limit", type=int, default=1, help="Maximum question instances to load")
    p.add_argument("--out", default="")
    p.add_argument("--pretty", action="store_true")
    args = p.parse_args()

    try:
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
