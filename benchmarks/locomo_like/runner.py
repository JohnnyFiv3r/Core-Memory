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
from core_memory.retrieval.semantic_index import semantic_doctor
from core_memory.runtime.jobs import async_jobs_status, run_async_jobs
from core_memory.runtime.engine import process_turn_finalized
from core_memory.runtime.myelination import myelination_report

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
    # Optional turn-preload path for larger LOCOMO-style traces.
    turns = list(setup.get("turns") or [])
    for i, t in enumerate(turns, start=1):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("turn_id") or f"fx-turn-{i}").strip() or f"fx-turn-{i}"
        sid = str(t.get("session_id") or "main").strip() or "main"
        uq = str(t.get("user_query") or "").strip()
        af = str(t.get("assistant_final") or "").strip()
        if not uq or not af:
            continue
        process_turn_finalized(
            root=root,
            session_id=sid,
            turn_id=tid,
            transaction_id=str(t.get("transaction_id") or f"tx-{tid}").strip() or f"tx-{tid}",
            trace_id=str(t.get("trace_id") or f"tr-{tid}").strip() or f"tr-{tid}",
            user_query=uq,
            assistant_final=af,
            metadata=dict(t.get("metadata") or {}),
            tools_trace=list(t.get("tools_trace") or []),
            mesh_trace=list(t.get("mesh_trace") or []),
            origin=str(t.get("origin") or "BENCHMARK_TURN").strip() or "BENCHMARK_TURN",
        )

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

    # Optional Dreamer fixture hooks for DV2 benchmark correlation paths.
    dreamer_associations = list(setup.get("dreamer_associations") or [])
    if dreamer_associations:
        from core_memory.runtime.dreamer_candidates import enqueue_dreamer_candidates, list_dreamer_candidates, decide_dreamer_candidate

        # Resolve bead keys in association rows.
        materialized_assoc: list[dict[str, Any]] = []
        for a in dreamer_associations:
            if not isinstance(a, dict):
                continue
            src = str(a.get("source") or bead_keys.get(str(a.get("source_key") or "")) or "").strip()
            tgt = str(a.get("target") or bead_keys.get(str(a.get("target_key") or "")) or "").strip()
            if not src or not tgt:
                continue
            row = dict(a)
            row["source"] = src
            row["target"] = tgt
            materialized_assoc.append(row)

        if materialized_assoc:
            enqueue_dreamer_candidates(
                root=root,
                associations=materialized_assoc,
                run_metadata={"run_id": f"bench-{case.id}", "mode": "benchmark_fixture", "source": "benchmark_fixture"},
            )

            auto_accept = {str(x).strip().lower() for x in (setup.get("dreamer_auto_accept") or []) if str(x).strip()}
            if auto_accept:
                pending = (list_dreamer_candidates(root=root, status="pending", limit=200).get("results") or [])
                for c in pending:
                    ht = str(c.get("hypothesis_type") or "").strip().lower()
                    if ht not in auto_accept:
                        continue
                    decide_dreamer_candidate(
                        root=root,
                        candidate_id=str(c.get("id") or ""),
                        decision="accept",
                        reviewer="benchmark-fixture",
                        notes="fixture_auto_accept",
                        apply=True,
                    )


def _read_turn_rows(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except Exception:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


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


def _queue_snapshot(root: str) -> dict[str, Any]:
    st = async_jobs_status(root)
    if not isinstance(st, dict):
        return {"ok": False, "pending_total": 0, "processable_now": 0}
    return {
        "ok": bool(st.get("ok")),
        "pending_total": int(st.get("pending_total") or 0),
        "processable_now": int(st.get("processable_now") or 0),
        "queues": dict(st.get("queues") or {}),
    }


def _dreamer_candidates_path(root: str) -> Path:
    return Path(root) / ".beads" / "events" / "dreamer-candidates.json"


def _load_dreamer_candidates(root: str) -> list[dict[str, Any]]:
    p = _dreamer_candidates_path(root)
    if not p.exists():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        return [dict(r or {}) for r in payload] if isinstance(payload, list) else []
    except Exception:
        return []


def _collect_edges(chains: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for c in chains or []:
        if not isinstance(c, dict):
            continue
        for e in (c.get("edges") or []):
            if not isinstance(e, dict):
                continue
            src = str(e.get("src") or e.get("source") or "").strip()
            dst = str(e.get("dst") or e.get("target") or "").strip()
            rel = str(e.get("rel") or e.get("relationship") or "").strip()
            if not src or not dst or not rel:
                continue
            key = (src, dst, rel)
            if key in seen:
                continue
            seen.add(key)
            out.append({"src": src, "dst": dst, "rel": rel})
    return out


def _correlate_dreamer_case(root: str, out: dict[str, Any]) -> dict[str, Any]:
    rows = _load_dreamer_candidates(root)
    accepted = [r for r in rows if str(r.get("status") or "").strip().lower() == "accepted"]
    accepted_applied = [r for r in accepted if bool(((r.get("applied") or {}).get("ok")))]

    result_ids = {str(r.get("bead_id") or "") for r in (out.get("results") or []) if str(r.get("bead_id") or "")}
    chain_edges = {
        (str(e.get("src") or ""), str(e.get("dst") or ""), str(e.get("rel") or ""))
        for e in _collect_edges(list(out.get("chains") or []))
    }

    used_ids: list[str] = []
    used_applied_ids: list[str] = []
    for r in accepted:
        cid = str(r.get("id") or "")
        src = str(r.get("source_bead_id") or "")
        tgt = str(r.get("target_bead_id") or "")
        rel = str(r.get("relationship") or "")
        used = False
        if src and src in result_ids:
            used = True
        if tgt and tgt in result_ids:
            used = True
        if (src, tgt, rel) in chain_edges or (tgt, src, rel) in chain_edges:
            used = True
        if used:
            used_ids.append(cid)
            if bool(((r.get("applied") or {}).get("ok"))):
                used_applied_ids.append(cid)

    return {
        "accepted_total": int(len(accepted)),
        "accepted_applied_total": int(len(accepted_applied)),
        "accepted_used_total": int(len(used_ids)),
        "accepted_applied_used_total": int(len(used_applied_ids)),
        "accepted_used_candidate_ids": sorted(set(used_ids)),
    }


def _benchmark_backend_mode(diag: dict[str, Any], *, semantic_mode: str) -> str:
    backend = str(diag.get("backend") or "not_built").strip().lower()
    usable = bool(diag.get("usable_backend"))
    if not usable:
        if str(semantic_mode) == "required":
            return "strict_missing_backend"
        return "degraded_lexical"
    if backend.startswith("faiss") or backend.startswith("chroma"):
        return "local_single_writer"
    if backend in {"qdrant", "pgvector"}:
        return "external_distributed"
    return "unknown"


def _estimate_tokens(text: str) -> int:
    s = str(text or "")
    if not s:
        return 0
    # Lightweight, model-agnostic estimate for benchmark observability.
    return max(1, int(round(len(s) / 4.0)))


def _case_token_usage(*, root: str, query: str, out: dict[str, Any]) -> dict[str, Any]:
    results = list(out.get("results") or [])
    result_ids = [str(r.get("bead_id") or "") for r in results if str(r.get("bead_id") or "")]

    retrieved_text_parts: list[str] = []
    try:
        idx = json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))
        beads = dict((idx.get("beads") or {})) if isinstance(idx, dict) else {}
        for bid in result_ids[:10]:
            b = dict(beads.get(bid) or {})
            if not b:
                continue
            retrieved_text_parts.append(str(b.get("title") or ""))
            retrieved_text_parts.extend([str(x) for x in (b.get("summary") or [])])
            retrieved_text_parts.append(str(b.get("detail") or ""))
    except Exception:
        pass

    answer_text = str(((out.get("answer_candidate") or {}).get("text") or ""))

    q_toks = _estimate_tokens(query)
    r_toks = _estimate_tokens("\n".join(retrieved_text_parts))
    a_toks = _estimate_tokens(answer_text)

    return {
        "mode": "estimated_char_4",
        "query_tokens_est": int(q_toks),
        "retrieved_context_tokens_est": int(r_toks),
        "answer_tokens_est": int(a_toks),
        "total_tokens_est": int(q_toks + r_toks + a_toks),
        "result_count": int(len(results)),
    }


def run_case(
    *,
    case: BenchmarkCase,
    gold: GoldCase,
    async_profile: str = "drain_before_query",
    semantic_mode: str = "degraded_allowed",
    vector_backend: str = "local-faiss",
    myelination_enabled: bool = False,
    preload_turns: list[dict[str, Any]] | None = None,
    benchmark_root: str | None = None,
) -> dict[str, Any]:
    t0_total = time.perf_counter()
    if benchmark_root:
        td = str(benchmark_root)
        Path(td).mkdir(parents=True, exist_ok=True)

        t_setup = time.perf_counter()
        if preload_turns:
            preload_setup = BenchmarkCase(
                id=f"preload-{case.id}",
                query=case.query,
                intent=case.intent,
                bucket_labels=case.bucket_labels,
                gold_id=case.gold_id,
                setup={"turns": list(preload_turns)},
                constraints=case.constraints,
                k=case.k,
            )
            _materialize_case(td, preload_setup)
        _materialize_case(td, case)
        setup_ms = (time.perf_counter() - t_setup) * 1000.0

        queue_after_setup = _queue_snapshot(td)
        queue_drained = None
        if async_profile == "drain_before_query":
            queue_drained = run_async_jobs(root=td, run_semantic=True, max_compaction=25, max_side_effects=25)

        queue_before_query = _queue_snapshot(td)

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
            "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": str(semantic_mode),
            "CORE_MEMORY_VECTOR_BACKEND": str(vector_backend),
            "CORE_MEMORY_MYELINATION_ENABLED": "1" if bool(myelination_enabled) else "0",
        }
        with _env_overrides(env):
            backend_diag = semantic_doctor(Path(td))
            backend_mode = _benchmark_backend_mode(backend_diag, semantic_mode=str(semantic_mode))
            t_query = time.perf_counter()
            out = memory_tools.execute(req, root=td, explain=True)
            retrieval_ms = (time.perf_counter() - t_query) * 1000.0
            myelination_obs = myelination_report(td, since="30d", limit=1000, top=5)

        queue_after_query = _queue_snapshot(td)

        ok, checks = _evaluate_case(case=case, gold=gold, out=out, root=td)
        latency_ms = (time.perf_counter() - t0_total) * 1000.0
        dreamer_corr = _correlate_dreamer_case(td, out)
        token_usage = _case_token_usage(root=td, query=case.query, out=out)
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
            "write_setup_ms": round(setup_ms, 3),
            "retrieval_ms": round(retrieval_ms, 3),
            "warnings": list(out.get("warnings") or []),
            "top_source_surface": str(((out.get("results") or [{}])[0] or {}).get("source_surface") or ""),
            "top_anchor_reason": str(((out.get("results") or [{}])[0] or {}).get("anchor_reason") or ""),
            "queue_after_setup": queue_after_setup,
            "queue_before_query": queue_before_query,
            "queue_after_query": queue_after_query,
            "queue_drained": queue_drained,
            "semantic_backend": backend_diag,
            "benchmark_backend_mode": backend_mode,
            "dreamer_correlation": dreamer_corr,
            "myelination_enabled": bool(myelination_enabled),
            "myelination_stats": dict((myelination_obs or {}).get("stats") or {}),
            "preload_turn_count": int(len(preload_turns or [])),
            "token_usage": token_usage,
        }

    with tempfile.TemporaryDirectory(prefix="cm-bench-") as td:
        t_setup = time.perf_counter()
        if preload_turns:
            preload_setup = BenchmarkCase(
                id=f"preload-{case.id}",
                query=case.query,
                intent=case.intent,
                bucket_labels=case.bucket_labels,
                gold_id=case.gold_id,
                setup={"turns": list(preload_turns)},
                constraints=case.constraints,
                k=case.k,
            )
            _materialize_case(td, preload_setup)
        _materialize_case(td, case)
        setup_ms = (time.perf_counter() - t_setup) * 1000.0

        queue_after_setup = _queue_snapshot(td)
        queue_drained = None
        if async_profile == "drain_before_query":
            queue_drained = run_async_jobs(root=td, run_semantic=True, max_compaction=25, max_side_effects=25)

        queue_before_query = _queue_snapshot(td)

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
            "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": str(semantic_mode),
            "CORE_MEMORY_VECTOR_BACKEND": str(vector_backend),
            "CORE_MEMORY_MYELINATION_ENABLED": "1" if bool(myelination_enabled) else "0",
        }
        with _env_overrides(env):
            backend_diag = semantic_doctor(Path(td))
            backend_mode = _benchmark_backend_mode(backend_diag, semantic_mode=str(semantic_mode))
            t_query = time.perf_counter()
            out = memory_tools.execute(req, root=td, explain=True)
            retrieval_ms = (time.perf_counter() - t_query) * 1000.0
            myelination_obs = myelination_report(td, since="30d", limit=1000, top=5)

        queue_after_query = _queue_snapshot(td)

        ok, checks = _evaluate_case(case=case, gold=gold, out=out, root=td)
        latency_ms = (time.perf_counter() - t0_total) * 1000.0
        dreamer_corr = _correlate_dreamer_case(td, out)
        token_usage = _case_token_usage(root=td, query=case.query, out=out)
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
            "write_setup_ms": round(setup_ms, 3),
            "retrieval_ms": round(retrieval_ms, 3),
            "warnings": list(out.get("warnings") or []),
            "top_source_surface": str(((out.get("results") or [{}])[0] or {}).get("source_surface") or ""),
            "top_anchor_reason": str(((out.get("results") or [{}])[0] or {}).get("anchor_reason") or ""),
            "queue_after_setup": queue_after_setup,
            "queue_before_query": queue_before_query,
            "queue_after_query": queue_after_query,
            "queue_drained": queue_drained,
            "semantic_backend": backend_diag,
            "benchmark_backend_mode": backend_mode,
            "dreamer_correlation": dreamer_corr,
            "myelination_enabled": bool(myelination_enabled),
            "myelination_stats": dict((myelination_obs or {}).get("stats") or {}),
            "preload_turn_count": int(len(preload_turns or [])),
            "token_usage": token_usage,
        }


def run_benchmark(
    *,
    fixtures_dir: Path,
    gold_dir: Path,
    subset: str = "local",
    limit: int | None = None,
    async_profile: str = "drain_before_query",
    semantic_mode: str = "degraded_allowed",
    vector_backend: str = "local-faiss",
    myelination_mode: str = "off",
    preload_turns_file: Path | None = None,
    benchmark_root: str | None = None,
) -> dict[str, Any]:
    pairs = build_cases(fixtures_dir=fixtures_dir, gold_dir=gold_dir)
    pairs = sorted(pairs, key=lambda p: p[0].id)

    if subset == "local":
        pairs = pairs[: min(len(pairs), 6)]
    if limit is not None:
        pairs = pairs[: max(1, int(limit))]

    mode_n = str(myelination_mode or "off").strip().lower()
    if mode_n not in {"off", "on", "compare"}:
        mode_n = "off"

    preload_turns = _read_turn_rows(preload_turns_file) if preload_turns_file else []

    def _run_results(enabled: bool) -> list[dict[str, Any]]:
        out_rows: list[dict[str, Any]] = []
        for case, gold in pairs:
            out_rows.append(
                run_case(
                    case=case,
                    gold=gold,
                    async_profile=async_profile,
                    semantic_mode=semantic_mode,
                    vector_backend=vector_backend,
                    myelination_enabled=bool(enabled),
                    preload_turns=preload_turns,
                    benchmark_root=benchmark_root,
                )
            )
        return out_rows

    if mode_n == "compare":
        baseline_results = _run_results(False)
        enabled_results = _run_results(True)
        case_results = enabled_results
    else:
        baseline_results = []
        case_results = _run_results(mode_n == "on")

    backend_modes = sorted(set(str(c.get("benchmark_backend_mode") or "") for c in case_results if str(c.get("benchmark_backend_mode") or "")))

    metadata = {
        "runner": "locomo_like",
        "subset": subset,
        "case_count": len(case_results),
        "commit": _repo_commit(),
        "semantic_mode": str(semantic_mode),
        "backend_mode": str(vector_backend),
        "benchmark_backend_modes": backend_modes,
        "semantic_mode_requested": str(semantic_mode),
        "async_profile": async_profile,
        "myelination_mode": mode_n,
        "myelination_enabled": bool(mode_n == "on"),
        "preload_turns_file": str(preload_turns_file) if preload_turns_file else "",
        "preload_turn_count": int(len(preload_turns)),
        "benchmark_root": str(benchmark_root or ""),
        "notes": ["proxy_fixture_pack", "deterministic_local_subset", "queue_visibility_enabled"],
    }

    report = build_report(metadata=metadata, case_results=case_results)

    if mode_n == "compare":
        base_meta = dict(metadata)
        base_meta["myelination_mode"] = "off"
        base_meta["myelination_enabled"] = False
        baseline_report = build_report(metadata=base_meta, case_results=baseline_results)

        enabled_totals = dict(report.get("totals") or {})
        baseline_totals = dict(baseline_report.get("totals") or {})

        by_case_base = {str(r.get("case_id") or ""): dict(r) for r in baseline_results}
        by_case_on = {str(r.get("case_id") or ""): dict(r) for r in case_results}
        per_case: list[dict[str, Any]] = []
        for cid in sorted(set(by_case_base.keys()).union(by_case_on.keys())):
            b = by_case_base.get(cid) or {}
            e = by_case_on.get(cid) or {}
            per_case.append(
                {
                    "case_id": cid,
                    "baseline_pass": bool(b.get("pass")),
                    "enabled_pass": bool(e.get("pass")),
                    "pass_changed": bool(b.get("pass")) != bool(e.get("pass")),
                    "baseline_latency_ms": float(b.get("latency_ms") or 0.0),
                    "enabled_latency_ms": float(e.get("latency_ms") or 0.0),
                    "latency_delta_ms": round(float(e.get("latency_ms") or 0.0) - float(b.get("latency_ms") or 0.0), 3),
                }
            )

        report["myelination_comparison"] = {
            "baseline": {
                "accuracy": float(baseline_totals.get("accuracy") or 0.0),
                "pass": int(baseline_totals.get("pass") or 0),
                "fail": int(baseline_totals.get("fail") or 0),
            },
            "enabled": {
                "accuracy": float(enabled_totals.get("accuracy") or 0.0),
                "pass": int(enabled_totals.get("pass") or 0),
                "fail": int(enabled_totals.get("fail") or 0),
            },
            "accuracy_delta": round(float(enabled_totals.get("accuracy") or 0.0) - float(baseline_totals.get("accuracy") or 0.0), 4),
            "cases": per_case,
        }

    return report


def main() -> int:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="Run LOCOMO-like benchmark harness")
    p.add_argument("--fixtures", default=str(here / "fixtures"))
    p.add_argument("--gold", default=str(here / "gold"))
    p.add_argument("--subset", choices=["local", "full"], default="local")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--async-profile", choices=["drain_before_query", "observe_only"], default="drain_before_query")
    p.add_argument("--semantic-mode", choices=["degraded_allowed", "required"], default="degraded_allowed")
    p.add_argument("--vector-backend", choices=["local-faiss", "qdrant", "pgvector", "chromadb"], default="local-faiss")
    p.add_argument("--myelination", choices=["off", "on", "compare"], default="off")
    p.add_argument("--preload-turns", default="", help="Optional JSONL of canonical turn-finalized rows to preload per case")
    p.add_argument("--out", default="")
    args = p.parse_args()

    report = run_benchmark(
        fixtures_dir=Path(args.fixtures),
        gold_dir=Path(args.gold),
        subset=str(args.subset),
        limit=args.limit,
        async_profile=str(args.async_profile),
        semantic_mode=str(args.semantic_mode),
        vector_backend=str(args.vector_backend),
        myelination_mode=str(args.myelination),
        preload_turns_file=(Path(args.preload_turns) if str(args.preload_turns or "").strip() else None),
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
