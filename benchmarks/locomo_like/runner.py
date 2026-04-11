from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core_memory.claim.resolver import resolve_all_current_state
from core_memory.persistence.store import MemoryStore
from core_memory.persistence.store_claim_ops import write_claim_updates_to_bead, write_claims_to_bead
from core_memory.retrieval.tools import memory as memory_tools

from .reporting import build_report, render_summary
from .schema import BenchmarkCase, GoldCase, build_cases


def _repo_commit() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
        return out or "unknown"
    except Exception:
        return "unknown"


@contextmanager
def _env_overrides(extra: dict[str, str]):
    saved = {k: os.environ.get(k) for k in extra.keys()}
    try:
        for k, v in extra.items():
            os.environ[k] = str(v)
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def _materialize_case(root: str, case: BenchmarkCase) -> None:
    s = MemoryStore(root)
    bead_keys: dict[str, str] = {}

    setup = dict(case.setup or {})
    beads = list(setup.get("beads") or [])

    for row in beads:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        bead_id = s.add_bead(
            type=str(row.get("type") or "context"),
            title=str(row.get("title") or "fixture bead"),
            summary=list(row.get("summary") or ["fixture"]),
            detail=str(row.get("detail") or ""),
            session_id=str(row.get("session_id") or "main"),
            source_turn_ids=list(row.get("source_turn_ids") or ["fx-turn"]),
            tags=list(row.get("tags") or []),
            status=str(row.get("status") or "open"),
        )
        if key:
            bead_keys[key] = bead_id

    for row in list(setup.get("claims") or []):
        if not isinstance(row, dict):
            continue
        key = str(row.get("bead_key") or "").strip()
        bead_id = bead_keys.get(key) if key else None
        if not bead_id and beads:
            fallback_key = str((beads[0] or {}).get("key") or "").strip()
            bead_id = bead_keys.get(fallback_key)
        if not bead_id:
            continue
        write_claims_to_bead(root, bead_id, list(row.get("rows") or []))

    for row in list(setup.get("claim_updates") or []):
        if not isinstance(row, dict):
            continue
        key = str(row.get("bead_key") or "").strip()
        bead_id = bead_keys.get(key) if key else None
        if not bead_id and beads:
            fallback_key = str((beads[0] or {}).get("key") or "").strip()
            bead_id = bead_keys.get(fallback_key)
        if not bead_id:
            continue
        write_claim_updates_to_bead(root, bead_id, list(row.get("rows") or []))


def _evaluate_case(*, case: BenchmarkCase, gold: GoldCase, out: dict[str, Any], root: str) -> tuple[bool, dict[str, bool]]:
    checks: dict[str, bool] = {}

    checks["answer_class"] = str(out.get("answer_outcome") or "") == str(gold.expected_answer_class or "")

    if gold.expected_source_surface:
        first = (out.get("results") or [{}])[0] if (out.get("results") or []) else {}
        checks["source_surface"] = str(first.get("source_surface") or "") == str(gold.expected_source_surface)

    if gold.expected_slot:
        state = resolve_all_current_state(root)
        slot_row = (state.get("slots") or {}).get(str(gold.expected_slot))
        checks["slot_present"] = bool(slot_row and slot_row.get("current_claim"))

    overall = all(checks.values()) if checks else False
    return overall, checks


def run_case(*, case: BenchmarkCase, gold: GoldCase) -> dict[str, Any]:
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="cm-bench-") as td:
        _materialize_case(td, case)

        req = {
            "raw_query": case.query,
            "intent": case.intent,
            "constraints": dict(case.constraints or {}),
            "k": int(case.k),
        }

        env = {
            "CORE_MEMORY_CLAIM_LAYER": "1",
            "CORE_MEMORY_CLAIM_RESOLUTION": "1",
            "CORE_MEMORY_CLAIM_RETRIEVAL_BOOST": "1",
            "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
        }
        with _env_overrides(env):
            out = memory_tools.execute(req, root=td, explain=True)

        ok, checks = _evaluate_case(case=case, gold=gold, out=out, root=td)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "case_id": case.id,
            "bucket_labels": list(case.bucket_labels),
            "query": case.query,
            "expected_answer_class": gold.expected_answer_class,
            "actual_answer_class": str(out.get("answer_outcome") or ""),
            "retrieval_mode": str(out.get("retrieval_mode") or ""),
            "pass": bool(ok),
            "checks": checks,
            "latency_ms": round(latency_ms, 3),
            "warnings": list(out.get("warnings") or []),
            "top_source_surface": str(((out.get("results") or [{}])[0] or {}).get("source_surface") or ""),
            "top_anchor_reason": str(((out.get("results") or [{}])[0] or {}).get("anchor_reason") or ""),
        }


def run_benchmark(
    *,
    fixtures_dir: Path,
    gold_dir: Path,
    subset: str = "local",
    limit: int | None = None,
) -> dict[str, Any]:
    pairs = build_cases(fixtures_dir=fixtures_dir, gold_dir=gold_dir)
    pairs = sorted(pairs, key=lambda p: p[0].id)

    if subset == "local":
        pairs = pairs[: min(len(pairs), 6)]
    if limit is not None:
        pairs = pairs[: max(1, int(limit))]

    case_results: list[dict[str, Any]] = []
    for case, gold in pairs:
        case_results.append(run_case(case=case, gold=gold))

    metadata = {
        "runner": "locomo_like",
        "subset": subset,
        "case_count": len(case_results),
        "commit": _repo_commit(),
        "semantic_mode": os.environ.get("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", "degraded_allowed"),
        "backend_mode": os.environ.get("CORE_MEMORY_VECTOR_BACKEND", "local-faiss"),
        "notes": ["proxy_fixture_pack", "deterministic_local_subset"],
    }

    return build_report(metadata=metadata, case_results=case_results)


def main() -> int:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="Run LOCOMO-like benchmark harness")
    p.add_argument("--fixtures", default=str(here / "fixtures"))
    p.add_argument("--gold", default=str(here / "gold"))
    p.add_argument("--subset", choices=["local", "full"], default="local")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", default="")
    args = p.parse_args()

    report = run_benchmark(
        fixtures_dir=Path(args.fixtures),
        gold_dir=Path(args.gold),
        subset=str(args.subset),
        limit=args.limit,
    )

    print(render_summary(report))
    print(json.dumps(report, indent=2))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
