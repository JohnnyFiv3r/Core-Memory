from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from benchmarks.causal.reporting import build_report as build_causal_report
from benchmarks.causal.runner import (
    _benchmark_env,
    _env_overrides,
    _evaluate_case,
    _materialize_case,
    _repo_commit,
    run_case as run_core_memory_case,
)
from benchmarks.causal.schema import CausalCase, CausalGold, build_cases
from benchmarks.contracts import BenchmarkShortcutFlags

_SUPPORTED_STRATEGIES = (
    "core_memory_full",
    "bm25",
    "similarity_only",
    "dense_vector",
    "long_context_no_memory",
    "external_memory_adapter",
)
_UNAVAILABLE_BASELINES: dict[str, dict[str, str]] = {
    "external_memory_adapter": {
        "baseline_kind": "external_memory_adapter",
        "availability": "requires_external_memory_adapter",
        "reason": "external_memory_adapter_not_configured",
    },
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def available_strategies() -> tuple[str, ...]:
    return _SUPPORTED_STRATEGIES


def _shortcut_block() -> dict[str, Any]:
    flags = BenchmarkShortcutFlags()
    return flags.to_dict()


def _select_pairs(
    *,
    fixtures_dir: Path,
    gold_dir: Path,
    subset: str,
    limit: int | None,
) -> list[tuple[CausalCase, CausalGold]]:
    pairs = sorted(build_cases(fixtures_dir=fixtures_dir, gold_dir=gold_dir), key=lambda p: p[0].id)
    if subset == "local":
        pairs = pairs[: min(len(pairs), 4)]
    if limit is not None:
        pairs = pairs[: max(1, int(limit))]
    return pairs


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _bead_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("title", "detail"):
        val = row.get(key)
        if val:
            parts.append(str(val))
    for key in ("summary", "entities", "topics", "tags"):
        val = row.get(key)
        if isinstance(val, list):
            parts.extend(str(x) for x in val if str(x).strip())
        elif val:
            parts.append(str(val))
    return " ".join(parts)


def _bm25_scores(query: str, docs: list[tuple[str, str, str]]) -> dict[str, float]:
    query_terms = _tokens(query)
    doc_terms = {key: _tokens(text) for key, _bid, text in docs}
    doc_lens = {key: len(terms) for key, terms in doc_terms.items()}
    avg_len = (sum(doc_lens.values()) / len(doc_lens)) if doc_lens else 1.0
    n_docs = max(1, len(doc_terms))

    dfs: Counter[str] = Counter()
    for terms in doc_terms.values():
        dfs.update(set(terms))

    k1 = 1.2
    b = 0.75
    scores: dict[str, float] = {}
    for key, terms in doc_terms.items():
        tf = Counter(terms)
        doc_len = max(1, doc_lens.get(key, 0))
        score = 0.0
        for term in query_terms:
            freq = tf.get(term, 0)
            if not freq:
                continue
            df = dfs.get(term, 0)
            idf = math.log(1.0 + ((n_docs - df + 0.5) / (df + 0.5)))
            denom = freq + k1 * (1.0 - b + b * (doc_len / max(avg_len, 1.0)))
            score += idf * ((freq * (k1 + 1.0)) / max(denom, 1e-9))
        scores[key] = float(score)
    return scores


def _char_ngrams(text: str, n: int = 3) -> Counter[str]:
    cleaned = " ".join(_tokens(text))
    if len(cleaned) < n:
        return Counter([cleaned]) if cleaned else Counter()
    return Counter(cleaned[i : i + n] for i in range(0, len(cleaned) - n + 1))


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(float(v) * float(b.get(k, 0)) for k, v in a.items())
    norm_a = math.sqrt(sum(float(v) * float(v) for v in a.values()))
    norm_b = math.sqrt(sum(float(v) * float(v) for v in b.values()))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def _similarity_scores(query: str, docs: list[tuple[str, str, str]]) -> dict[str, float]:
    q_terms = Counter(_tokens(query))
    q_grams = _char_ngrams(query)
    scores: dict[str, float] = {}
    for key, _bid, text in docs:
        term_score = _cosine(q_terms, Counter(_tokens(text)))
        gram_score = _cosine(q_grams, _char_ngrams(text))
        scores[key] = float((0.65 * term_score) + (0.35 * gram_score))
    return scores


def _long_context_proxy_scores(query: str, docs: list[tuple[str, str, str]]) -> dict[str, float]:
    """Graph-blind local proxy for a context-window/no-memory baseline."""
    q_terms = Counter(_tokens(query))
    q_grams = _char_ngrams(query)
    context_terms = Counter(_tokens(" ".join(text for _key, _bid, text in docs)))
    scores: dict[str, float] = {}
    for key, _bid, text in docs:
        terms = Counter(_tokens(text))
        term_score = _cosine(q_terms, terms)
        gram_score = _cosine(q_grams, _char_ngrams(text))
        context_presence = 0.0
        if q_terms:
            context_presence = sum(1.0 for term in q_terms if terms.get(term) and context_terms.get(term)) / float(len(q_terms))
        scores[key] = float((0.55 * term_score) + (0.25 * gram_score) + (0.20 * context_presence))
    return scores


def _unavailable_strategy_report(*, strategy: str, subset: str) -> dict[str, Any]:
    info = dict(_UNAVAILABLE_BASELINES[strategy])
    flags = _shortcut_block()
    reason = str(info.get("reason") or "baseline_unavailable")
    metadata = {
        "runner": "causal_continuity.t1",
        "task_id": "t1_causal_chain_reconstruction",
        "strategy": strategy,
        "status": "unavailable",
        "baseline_kind": str(info.get("baseline_kind") or strategy),
        "availability": str(info.get("availability") or "unavailable"),
        "unavailable_reason": reason,
        "execution_mode": "not_configured",
        "adapter_status": "unavailable",
        "adapter_name": "",
        "uses_causal_traversal": False,
        "leaderboard_claim": False,
        "subset": subset,
        "case_count": 0,
        "commit": _repo_commit(),
        "faithfulness": flags,
        "shortcut_flags": flags,
        "notes": [
            "baseline_row_declared",
            "not_executed",
            "no_causal_edges_used_for_ranking",
        ],
    }
    report = build_causal_report(metadata=metadata, case_results=[])
    report["warnings"] = sorted(set(list(report.get("warnings") or []) + [reason]))
    return report


def _ranked_payload(
    *,
    case: CausalCase,
    key_to_id: dict[str, str],
    score_fn: Callable[[str, list[tuple[str, str, str]]], dict[str, float]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    docs: list[tuple[str, str, str]] = []
    for row in case.beads:
        key = str(row.get("key") or "").strip()
        bid = key_to_id.get(key, "")
        if key and bid:
            docs.append((key, bid, _bead_text(row)))

    scores = score_fn(case.query, docs)
    ranked = sorted(
        docs,
        key=lambda d: (-float(scores.get(d[0]) or 0.0), d[0]),
    )
    ranked = ranked[: max(1, int(case.k))]

    evidence: list[dict[str, Any]] = []
    root_causes: list[dict[str, Any]] = []
    for rank, (key, bid, text) in enumerate(ranked, start=1):
        score = round(float(scores.get(key) or 0.0), 6)
        evidence.append({
            "bead_id": bid,
            "title": key,
            "content_excerpt": text[:240],
            "score": score,
            "rank": rank,
        })
        root_causes.append({
            "bead_id": bid,
            "score": score,
            "rank": rank,
        })

    payload = {
        "evidence": evidence,
        "root_cause_attribution": {
            "root_causes": root_causes,
            "causal_paths": [],
        },
        "tier_path": ["baseline_similarity"],
        "warnings": [],
    }
    return payload, evidence


def run_baseline_case(*, case: CausalCase, gold: CausalGold, strategy: str) -> dict[str, Any]:
    if strategy == "bm25":
        score_fn = _bm25_scores
    elif strategy in {"similarity_only", "dense_vector"}:
        score_fn = _similarity_scores
    elif strategy == "long_context_no_memory":
        score_fn = _long_context_proxy_scores
    elif strategy == "external_memory_adapter":
        score_fn = _similarity_scores
    else:
        raise ValueError(f"unsupported_t1_baseline_strategy:{strategy}")

    t0 = time.perf_counter()
    td = tempfile.mkdtemp(prefix=f"cm-t1-{strategy}-")
    try:
        with _env_overrides(_benchmark_env()):
            t_setup = time.perf_counter()
            key_to_id = _materialize_case(td, case)
            setup_ms = (time.perf_counter() - t_setup) * 1000.0

            t_query = time.perf_counter()
            payload, _evidence = _ranked_payload(case=case, key_to_id=key_to_id, score_fn=score_fn)
            retrieval_ms = (time.perf_counter() - t_query) * 1000.0

        metrics = _evaluate_case(case=case, gold=gold, payload=payload, key_to_id=key_to_id)
    finally:
        shutil.rmtree(td, ignore_errors=True)

    return {
        "case_id": case.id,
        "bucket_labels": list(case.bucket_labels),
        "query": case.query,
        "expected_grounding": gold.expected_grounding,
        "strategy": strategy,
        "write_setup_ms": round(setup_ms, 3),
        "retrieval_ms": round(retrieval_ms, 3),
        "tier_path": list(payload.get("tier_path") or []),
        "warnings": list(payload.get("warnings") or []),
        **metrics,
        "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3),
    }


def run_t1_strategy(
    *,
    pairs: list[tuple[CausalCase, CausalGold]],
    strategy: str,
    subset: str,
    external_memory_adapter: str | None = None,
) -> dict[str, Any]:
    if strategy not in _SUPPORTED_STRATEGIES:
        raise ValueError(f"unsupported_t1_strategy:{strategy}")

    configured_external_adapter = str(
        external_memory_adapter or os.environ.get("CORE_MEMORY_BENCHMARK_EXTERNAL_MEMORY_ADAPTER") or ""
    ).strip().lower()
    if strategy == "external_memory_adapter" and configured_external_adapter in {"fake", "fake_external_memory", "deterministic_fake"}:
        rows = [run_baseline_case(case=case, gold=gold, strategy=strategy) for case, gold in pairs]
        baseline_kind = "external_memory_adapter"
        status = "adapter_executed"
        availability = "configured_test_adapter"
        execution_mode = "adapter_fake"
        adapter_status = "completed"
        adapter_name = "fake_external_memory_adapter"
        uses_causal_traversal = False
        notes = [
            "external_memory_adapter_contract_exercised",
            "fake_adapter_for_offline_tests",
            "no_causal_edges_used_for_ranking",
        ]
    elif strategy in _UNAVAILABLE_BASELINES:
        return _unavailable_strategy_report(strategy=strategy, subset=subset)
    elif strategy == "core_memory_full":
        rows = [dict(run_core_memory_case(case=case, gold=gold), strategy=strategy) for case, gold in pairs]
        baseline_kind = "causal_memory"
        status = "completed"
        availability = "executed"
        execution_mode = "core_memory"
        adapter_status = "not_required"
        adapter_name = ""
        uses_causal_traversal = True
        notes = ["public_write_path", "causal_traversal", "adversarial_distractors"]
    else:
        rows = [run_baseline_case(case=case, gold=gold, strategy=strategy) for case, gold in pairs]
        baseline_kind = {
            "bm25": "lexical",
            "similarity_only": "similarity_proxy",
            "dense_vector": "dense_vector_proxy",
            "long_context_no_memory": "long_context_local_proxy",
        }.get(strategy, "baseline")
        status = "completed" if strategy not in {"dense_vector", "long_context_no_memory"} else "proxy_executed"
        availability = "executed" if strategy != "dense_vector" else "local_deterministic_proxy"
        if strategy == "long_context_no_memory":
            availability = "local_context_window_proxy"
        execution_mode = "local_proxy" if strategy in {"dense_vector", "long_context_no_memory"} else "local_baseline"
        adapter_status = "proxy_executed" if strategy in {"dense_vector", "long_context_no_memory"} else "not_required"
        adapter_name = "local_long_context_no_memory_proxy" if strategy == "long_context_no_memory" else ""
        uses_causal_traversal = False
        notes = [
            "public_write_path_materialization",
            "no_causal_edges_used_for_ranking",
            "adversarial_distractors",
        ]
        if strategy == "dense_vector":
            notes.append("deterministic_similarity_proxy_for_dense_vector_row")
        if strategy == "long_context_no_memory":
            notes.append("deterministic_context_window_proxy_no_memory_state")

    flags = _shortcut_block()
    metadata = {
        "runner": "causal_continuity.t1",
        "task_id": "t1_causal_chain_reconstruction",
        "strategy": strategy,
        "status": status,
        "baseline_kind": baseline_kind,
        "availability": availability,
        "execution_mode": execution_mode,
        "adapter_status": adapter_status,
        "adapter_name": adapter_name,
        "uses_causal_traversal": bool(uses_causal_traversal),
        "leaderboard_claim": False,
        "subset": subset,
        "case_count": len(rows),
        "commit": _repo_commit(),
        "faithfulness": flags,
        "shortcut_flags": flags,
        "notes": notes,
    }
    return build_causal_report(metadata=metadata, case_results=rows)


def _strategy_matrix_row(report: dict[str, Any]) -> dict[str, Any]:
    meta = dict(report.get("metadata") or {})
    totals = dict(report.get("totals") or {})
    cm = dict(report.get("causal_metrics") or {})
    ds = dict(report.get("distractor_survival") or {})
    lat = dict(report.get("latency_ms") or {})
    return {
        "status": str(meta.get("status") or "completed"),
        "baseline_kind": str(meta.get("baseline_kind") or ""),
        "availability": str(meta.get("availability") or ""),
        "unavailable_reason": str(meta.get("unavailable_reason") or ""),
        "execution_mode": str(meta.get("execution_mode") or ""),
        "adapter_status": str(meta.get("adapter_status") or ""),
        "adapter_name": str(meta.get("adapter_name") or ""),
        "uses_causal_traversal": bool(meta.get("uses_causal_traversal")),
        "leaderboard_claim": bool(meta.get("leaderboard_claim")),
        "cases": int(totals.get("cases") or 0),
        "pass": int(totals.get("pass") or 0),
        "accuracy": float(totals.get("accuracy") or 0.0),
        "causal_survival_rate": float(ds.get("survival_rate") or 0.0),
        "adversarial_case_count": int(ds.get("adversarial_case_count") or 0),
        "root_cause_accuracy": float(cm.get("root_cause_accuracy") or 0.0),
        "grounding_full_rate": float(cm.get("grounding_full_rate") or 0.0),
        "edge_precision_mean": float(cm.get("edge_precision_mean") or 0.0),
        "edge_recall_mean": float(cm.get("edge_recall_mean") or 0.0),
        "edge_f1_mean": float(cm.get("edge_f1_mean") or 0.0),
        "latency_mean_ms": float(lat.get("mean") or 0.0),
        "retrieval_mean_ms": float(lat.get("retrieval_mean") or 0.0),
    }


def _case_matrix(strategy_reports: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by_case: dict[str, dict[str, Any]] = {}
    for strategy, report in strategy_reports.items():
        for row in (report.get("cases") or []):
            case_id = str(row.get("case_id") or "").strip()
            if not case_id:
                continue
            item = by_case.setdefault(case_id, {
                "case_id": case_id,
                "bucket_labels": list(row.get("bucket_labels") or []),
                "query": str(row.get("query") or ""),
                "strategies": {},
            })
            item["strategies"][strategy] = {
                "pass": bool(row.get("pass")),
                "edge_f1": float(row.get("edge_f1") or 0.0),
                "root_cause_correct": bool(row.get("root_cause_correct")),
                "distractor_survived": bool(row.get("distractor_survived")),
                "attribution_depth": int(row.get("attribution_depth") or 0),
            }
    return [by_case[k] for k in sorted(by_case)]


def run_t1_matrix(
    *,
    fixtures_dir: Path,
    gold_dir: Path,
    strategies: list[str] | tuple[str, ...],
    subset: str = "full",
    limit: int | None = None,
    external_memory_adapter: str | None = None,
) -> dict[str, Any]:
    pairs = _select_pairs(fixtures_dir=fixtures_dir, gold_dir=gold_dir, subset=subset, limit=limit)
    strategy_reports: dict[str, dict[str, Any]] = {}
    for strategy in strategies:
        normalized = str(strategy or "").strip()
        if not normalized:
            continue
        strategy_reports[normalized] = run_t1_strategy(
            pairs=pairs,
            strategy=normalized,
            subset=subset,
            external_memory_adapter=external_memory_adapter,
        )

    return {
        "schema_version": "causal_continuity.t1_matrix.v1",
        "task_id": "t1_causal_chain_reconstruction",
        "capability": "C1_causal_attribution",
        "subset": subset,
        "case_count": len(pairs),
        "strategies": list(strategy_reports.keys()),
        "strategy_matrix": {
            name: _strategy_matrix_row(report)
            for name, report in sorted(strategy_reports.items())
        },
        "case_matrix": _case_matrix(strategy_reports),
        "strategy_reports": strategy_reports,
    }
