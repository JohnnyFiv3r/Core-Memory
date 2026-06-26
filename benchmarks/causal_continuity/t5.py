from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from core_memory.graph.storylines import derive_storylines
from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline.canonical import trace_request

from benchmarks.causal.runner import _env_overrides, _repo_commit
from benchmarks.contracts import BenchmarkShortcutFlags

from .judges import build_answerability_judge

T5_FIXTURE_SCHEMA = "causal_continuity.t5_fixture.v1"
T5_REPORT_SCHEMA = "causal_continuity.t5_thread_fidelity.v1"

_STOPWORDS = {
    "a",
    "an",
    "and",
    "after",
    "because",
    "did",
    "for",
    "how",
    "in",
    "of",
    "or",
    "the",
    "to",
    "was",
    "were",
    "why",
}


def default_fixture_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "t5_thread_fidelity.json"


def _load_fixture(path: Path | None = None) -> dict[str, Any]:
    p = path or default_fixture_path()
    payload = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"t5_fixture_not_object:{p}")
    if str(payload.get("schema") or "") != T5_FIXTURE_SCHEMA:
        raise ValueError(f"t5_fixture_schema_mismatch:{p}")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"t5_fixture_cases_invalid:{p}")
    return payload


def _stem(token: str) -> str:
    t = str(token or "").strip().lower()
    for suffix in ("ing", "ed", "es", "s"):
        if len(t) > len(suffix) + 3 and t.endswith(suffix):
            return t[: -len(suffix)]
    return t


def _tokens(text: Any) -> set[str]:
    out: set[str] = set()
    for raw in re.findall(r"[A-Za-z0-9_]+", str(text or "").lower()):
        token = _stem(raw)
        if len(token) > 2 and token not in _STOPWORDS:
            out.add(token)
    return out


def _stable_bead_key(bead: dict[str, Any], bead_id: str = "") -> str:
    for tag in list(bead.get("tags") or []):
        text = str(tag or "").strip()
        if text.startswith("benchmark_key:"):
            return text.split(":", 1)[1].strip() or bead_id
    for src in list(bead.get("source_turn_ids") or []):
        text = str(src or "").strip()
        if text:
            return text
    return str(bead.get("title") or bead_id)


def _materialize_case(root: str | Path, case: dict[str, Any]) -> dict[str, str]:
    store = MemoryStore(str(root))
    bead_keys: dict[str, str] = {}
    for row in list(case.get("beads") or []):
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        tags = list(row.get("tags") or ["benchmark_thread_fidelity"])
        if key:
            tags.append(f"benchmark_key:{key}")
        tags = _ordered_unique(tags)
        bead_id = store.add_bead(
            type=str(row.get("type") or "context"),
            title=str(row.get("title") or "thread fidelity fixture bead"),
            summary=list(row.get("summary") or ["thread fidelity fixture"]),
            detail=str(row.get("detail") or ""),
            session_id=str(row.get("session_id") or "t5"),
            source_turn_ids=list(row.get("source_turn_ids") or [f"fx-{key or 'bead'}"]),
            entities=list(row.get("entities") or []),
            topics=list(row.get("topics") or []),
            tags=tags,
        )
        if key:
            bead_keys[key] = bead_id

    for edge in list(case.get("edges") or []):
        if not isinstance(edge, dict):
            continue
        src = bead_keys.get(str(edge.get("source_key") or "").strip())
        tgt = bead_keys.get(str(edge.get("target_key") or "").strip())
        if not src or not tgt:
            continue
        store.link(
            source_id=src,
            target_id=tgt,
            relationship=str(edge.get("relationship") or "supports"),
            confidence=float(edge.get("confidence") if edge.get("confidence") is not None else 0.85),
            explanation=str(edge.get("explanation") or "benchmark thread edge"),
        )
    return bead_keys


def _read_beads(root: str | Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    beads = payload.get("beads") if isinstance(payload, dict) else {}
    return {str(k): dict(v) for k, v in dict(beads or {}).items() if isinstance(v, dict)}


def _bead_text(bead: dict[str, Any]) -> str:
    summary = " ".join(str(x) for x in list(bead.get("summary") or []))
    entities = " ".join(str(x) for x in list(bead.get("entities") or []))
    tags = " ".join(str(x) for x in list(bead.get("tags") or []))
    return " ".join([str(bead.get("title") or ""), summary, entities, tags])


def _ordered_unique(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _trace_bead_ids(trace: dict[str, Any]) -> list[str]:
    ids: list[Any] = []
    for row in list(trace.get("results") or []):
        if isinstance(row, dict):
            ids.append(row.get("bead_id"))
    for chain in list(trace.get("chains") or []):
        if not isinstance(chain, dict):
            continue
        ids.extend(list(chain.get("path") or []))
    return _ordered_unique(ids)


def _trace_anchor_ids(trace: dict[str, Any]) -> list[str]:
    ids: list[Any] = []
    for row in list(trace.get("anchors") or []):
        if isinstance(row, dict):
            ids.append(row.get("bead_id"))
    return _ordered_unique(ids)


def _storyline_rows(root: str | Path) -> list[dict[str, Any]]:
    out = derive_storylines(root, kinds=["entity", "goal", "claim"], min_length=2)
    rows: list[dict[str, Any]] = []
    for row in list(out.get("storylines") or []):
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _score_storyline(
    storyline: dict[str, Any],
    *,
    query_tokens: set[str],
    trace_ids: list[str],
    beads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    backbone = dict(storyline.get("backbone") or {})
    bead_ids = [str(x) for x in list(backbone.get("bead_ids") or []) if str(x).strip()]
    bead_ids = sorted(bead_ids, key=lambda bid: _stable_bead_key(beads.get(bid, {}), bid))
    trace_set = set(trace_ids)
    text_by_bead = {bid: _tokens(_bead_text(beads.get(bid, {}))) for bid in bead_ids}
    union_tokens: set[str] = set()
    for toks in text_by_bead.values():
        union_tokens.update(toks)
    coverage = (len(query_tokens & union_tokens) / float(len(query_tokens))) if query_tokens else 0.0
    per_bead = [
        (len(query_tokens & toks) / float(len(query_tokens)))
        for toks in text_by_bead.values()
        if query_tokens
    ]
    mean_bead_overlap = (sum(per_bead) / float(len(per_bead))) if per_bead else 0.0
    trace_support = (len(set(bead_ids) & trace_set) / float(len(bead_ids))) if bead_ids else 0.0
    score = (0.60 * coverage) + (0.25 * mean_bead_overlap) + (0.15 * trace_support)
    return {
        "storyline_id": str(storyline.get("id") or ""),
        "kind": str(backbone.get("kind") or ""),
        "label": str(backbone.get("label") or ""),
        "bead_ids": bead_ids,
        "bead_keys": [_stable_bead_key(beads.get(bid, {}), bid) for bid in bead_ids],
        "coverage": round(coverage, 4),
        "mean_bead_overlap": round(mean_bead_overlap, 4),
        "trace_support": round(trace_support, 4),
        "score": round(score, 6),
    }


def _select_storyline(
    *,
    storylines: list[dict[str, Any]],
    query_tokens: set[str],
    trace_ids: list[str],
    beads: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    scored = [
        _score_storyline(row, query_tokens=query_tokens, trace_ids=trace_ids, beads=beads)
        for row in storylines
    ]
    scored.sort(key=lambda r: (-float(r.get("score") or 0.0), -float(r.get("trace_support") or 0.0), str(r.get("storyline_id") or "")))
    return (scored[0] if scored else {}, scored)


def _one_shot_graph_blind_ids(case: dict[str, Any], bead_keys: dict[str, str], *, k: int) -> list[str]:
    query_tokens = _tokens(case.get("query") or "")
    rows: list[tuple[float, str, str]] = []
    for row in list(case.get("beads") or []):
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        bid = bead_keys.get(key, "")
        if not key or not bid:
            continue
        text = " ".join([
            str(row.get("title") or ""),
            " ".join(str(x) for x in list(row.get("summary") or [])),
            str(row.get("detail") or ""),
        ])
        row_tokens = _tokens(text)
        overlap = (len(query_tokens & row_tokens) / float(len(query_tokens))) if query_tokens else 0.0
        rows.append((overlap, key, bid))
    rows.sort(key=lambda item: (-item[0], item[1]))
    return [bid for _score, _key, bid in rows[: max(1, int(k))]]


def _run_graph_blind_loop(root: str | Path, case: dict[str, Any], bead_keys: dict[str, str]) -> dict[str, Any]:
    gold_count = max(1, len(list(case.get("gold_thread_keys") or [])))
    returned = _one_shot_graph_blind_ids(case, bead_keys, k=gold_count)
    key_by_id = {v: k for k, v in bead_keys.items()}
    return {
        "query": str(case.get("query") or ""),
        "intent": str(case.get("intent") or "causal"),
        "returned_thread_ids": returned,
        "selected_storyline": {
            "storyline_id": "graph_blind_one_shot",
            "kind": "baseline",
            "label": "graph_blind_one_shot",
            "bead_ids": returned,
            "bead_keys": [key_by_id.get(bid, bid) for bid in returned],
            "score": None,
        },
        "storyline_candidate_count": 0,
        "steps": [
            {
                "step": 1,
                "anchor_ids": [],
                "trace_anchor_ids": returned,
                "trace_ids": returned,
                "trace_grounding": {"mode": "disabled_traversal_graph_blind"},
                "selected_storyline": {
                    "storyline_id": "graph_blind_one_shot",
                    "bead_ids": returned,
                    "bead_keys": [key_by_id.get(bid, bid) for bid in returned],
                },
                "candidate_storylines": [],
                "stop_gate": {
                    "passed": True,
                    "reason": "graph_blind_one_shot_baseline",
                },
            }
        ],
        "methodology": {
            "kind": "graph_blind_one_shot_baseline",
            "is_llm_judge": False,
            "notes": [
                "no trace_request",
                "no causal traversal",
                "lexical title_summary_detail ranking only",
            ],
        },
    }


def _run_thread_loop(root: str | Path, case: dict[str, Any], loop_cfg: dict[str, Any]) -> dict[str, Any]:
    query = str(case.get("query") or "")
    intent = str(case.get("intent") or "causal")
    query_tokens = _tokens(query)
    max_steps = max(1, int(loop_cfg.get("max_steps") or 3))
    k = max(1, int(loop_cfg.get("k") or 5))
    max_depth = max(1, int(loop_cfg.get("max_depth") or 3))
    max_chains = max(1, int(loop_cfg.get("max_chains") or 8))
    beads = _read_beads(root)
    storylines = _storyline_rows(root)
    anchor_ids: list[str] | None = None
    steps: list[dict[str, Any]] = []
    selected: dict[str, Any] = {}
    scored: list[dict[str, Any]] = []

    for step in range(1, max_steps + 1):
        trace = trace_request(
            root=root,
            query=query,
            anchor_ids=anchor_ids,
            k=k,
            intent=intent,
            max_depth=max_depth,
            max_chains=max_chains,
        )
        trace_ids = _trace_bead_ids(trace)
        trace_anchor_ids = _trace_anchor_ids(trace)
        selected, scored = _select_storyline(
            storylines=storylines,
            query_tokens=query_tokens,
            trace_ids=trace_ids,
            beads=beads,
        )
        selected_ids = list(selected.get("bead_ids") or [])
        token_coverage = float(selected.get("coverage") or 0.0)
        stop = bool(selected_ids) and token_coverage >= 0.80 and float(selected.get("trace_support") or 0.0) >= 0.50
        steps.append({
            "step": step,
            "anchor_ids": list(anchor_ids or []),
            "trace_anchor_ids": trace_anchor_ids,
            "trace_ids": trace_ids,
            "trace_grounding": dict(trace.get("grounding") or {}),
            "selected_storyline": selected,
            "candidate_storylines": scored[:5],
            "stop_gate": {
                "passed": stop,
                "reason": "answerability_proxy_ready" if stop else "continue_query_anchored_expansion",
            },
        })
        if stop:
            break
        anchor_ids = selected_ids[:k] if selected_ids else trace_ids[:k]

    return {
        "query": query,
        "intent": intent,
        "returned_thread_ids": list(selected.get("bead_ids") or []),
        "selected_storyline": selected,
        "storyline_candidate_count": len(storylines),
        "steps": steps,
        "methodology": {
            "kind": "deterministic_trace_storyline_proxy",
            "is_llm_judge": False,
            "notes": [
                "trace_request supplies semantic seed plus causal expansion",
                "storyline selection is scored against the original query at each step",
                "answerability is scored post-hoc from gold labels for deterministic CI",
            ],
        },
    }


def _safe_rate(num: int, den: int) -> float:
    return round(num / float(den), 4) if den > 0 else 0.0


def _thread_metrics(returned: list[str], *, gold_ids: set[str], required_ids: set[str], drift_ids: set[str], max_query_drift_rate: float) -> dict[str, float]:
    returned_set = set(returned)
    true_positive = len(returned_set & gold_ids)
    precision = _safe_rate(true_positive, len(returned_set))
    recall = _safe_rate(true_positive, len(gold_ids))
    f1 = round((2 * precision * recall / (precision + recall)), 4) if (precision + recall) > 0 else 0.0
    drift_rate = _safe_rate(len(returned_set & drift_ids), len(returned_set))
    answerability = 1.0 if required_ids.issubset(returned_set) and drift_rate <= max_query_drift_rate else 0.0
    return {
        "thread_precision": precision,
        "thread_recall": recall,
        "thread_f1": f1,
        "answerability": answerability,
        "query_drift_rate": drift_rate,
    }


def _evaluate_case(
    case: dict[str, Any],
    *,
    bead_keys: dict[str, str],
    loop: dict[str, Any],
    targets: dict[str, Any],
    judge_kind: str | None = None,
) -> dict[str, Any]:
    returned = [str(x) for x in list(loop.get("returned_thread_ids") or []) if str(x).strip()]
    key_by_id = {v: k for k, v in bead_keys.items()}
    gold_keys = [str(x) for x in list(case.get("gold_thread_keys") or []) if str(x) in bead_keys]
    required_keys = [str(x) for x in list(case.get("required_answer_keys") or []) if str(x) in bead_keys]
    drift_keys = [str(x) for x in list(case.get("drift_thread_keys") or []) if str(x) in bead_keys]
    gold_ids = {bead_keys[k] for k in gold_keys}
    required_ids = {bead_keys[k] for k in required_keys}
    drift_ids = {bead_keys[k] for k in drift_keys}
    max_drift = float(targets.get("max_query_drift_rate") or 0.25)
    scored = _thread_metrics(returned, gold_ids=gold_ids, required_ids=required_ids, drift_ids=drift_ids, max_query_drift_rate=max_drift)
    one_shot_returned = _one_shot_graph_blind_ids(case, bead_keys, k=max(1, len(gold_ids)))
    one_shot_scored = _thread_metrics(
        one_shot_returned,
        gold_ids=gold_ids,
        required_ids=required_ids,
        drift_ids=drift_ids,
        max_query_drift_rate=max_drift,
    )
    checks = {
        "thread_precision": scored["thread_precision"] >= float(targets.get("min_thread_precision") or 0.0),
        "thread_recall": scored["thread_recall"] >= float(targets.get("min_thread_recall") or 0.0),
        "thread_f1": scored["thread_f1"] >= float(targets.get("min_thread_f1") or 0.0),
        "answerability": scored["answerability"] >= float(targets.get("min_answerability") or 1.0),
        "query_drift": scored["query_drift_rate"] <= max_drift,
    }
    judge = build_answerability_judge(judge_kind)
    returned_keys = [key_by_id.get(bid, bid) for bid in returned]
    judge_result = judge.judge_case(
        case=case,
        returned_keys=returned_keys,
        required_keys=required_keys,
        drift_keys=drift_keys,
        deterministic_metrics=scored,
    )
    return {
        "case_id": str(case.get("id") or ""),
        "query": str(case.get("query") or ""),
        "bead_key_by_id": key_by_id,
        "gold_thread_keys": gold_keys,
        "required_answer_keys": required_keys,
        "drift_thread_keys": drift_keys,
        "returned_thread_keys": returned_keys,
        "gold_thread_bead_ids": [bead_keys[k] for k in gold_keys],
        "required_answer_bead_ids": [bead_keys[k] for k in required_keys],
        "drift_thread_bead_ids": [bead_keys[k] for k in drift_keys],
        "returned_thread_bead_ids": returned,
        "metrics": scored,
        "judge_kind": str(judge_result.get("judge_kind") or judge.kind),
        "judge_status": str(judge_result.get("judge_status") or ""),
        "is_llm_judge": bool(judge_result.get("is_llm_judge")),
        "prompt_version": str(judge_result.get("prompt_version") or judge.prompt_version),
        "answerability_judge": judge_result,
        "one_shot_anchor_baseline": {
            "returned_bead_ids": one_shot_returned,
            "returned_keys": [key_by_id.get(bid, bid) for bid in one_shot_returned],
            "metrics": one_shot_scored,
        },
        "checks": checks,
        "pass": all(checks.values()),
        "loop": loop,
    }


def _mean(rows: list[float]) -> float:
    values = [float(v) for v in rows if not math.isnan(float(v))]
    return round(sum(values) / float(len(values)), 4) if values else 0.0


def run_t5_thread_fidelity(
    *,
    fixture_path: Path | None = None,
    traversal_enabled: bool = True,
    judge_kind: str | None = None,
) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    targets = dict(fixture.get("targets") or {})
    loop_cfg = dict(fixture.get("loop") or {})
    t0 = time.perf_counter()
    case_rows: list[dict[str, Any]] = []

    for case in list(fixture.get("cases") or []):
        if not isinstance(case, dict):
            continue
        td = tempfile.mkdtemp(prefix="cm-t5-thread-")
        try:
            with _env_overrides({
                "CORE_MEMORY_GRAPH_BACKEND": "none",
                "CORE_MEMORY_SEMANTIC_AUTODRAIN": "off",
                "CORE_MEMORY_VECTOR_BACKEND": "local-faiss",
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
            }):
                bead_keys = _materialize_case(td, case)
                loop = _run_thread_loop(td, case, loop_cfg) if traversal_enabled else _run_graph_blind_loop(td, case, bead_keys)
            case_rows.append(_evaluate_case(case, bead_keys=bead_keys, loop=loop, targets=targets, judge_kind=judge_kind))
        finally:
            shutil.rmtree(td, ignore_errors=True)

    metrics = {
        "case_count": len(case_rows),
        "pass_count": sum(1 for row in case_rows if bool(row.get("pass"))),
        "thread_precision": _mean([float((row.get("metrics") or {}).get("thread_precision") or 0.0) for row in case_rows]),
        "thread_recall": _mean([float((row.get("metrics") or {}).get("thread_recall") or 0.0) for row in case_rows]),
        "thread_f1": _mean([float((row.get("metrics") or {}).get("thread_f1") or 0.0) for row in case_rows]),
        "answerability": _mean([float((row.get("metrics") or {}).get("answerability") or 0.0) for row in case_rows]),
        "judge_answerability": _mean([
            float((row.get("answerability_judge") or {}).get("answerability") or 0.0)
            for row in case_rows
            if (row.get("answerability_judge") or {}).get("answerability") is not None
        ]),
        "query_drift_rate": _mean([float((row.get("metrics") or {}).get("query_drift_rate") or 0.0) for row in case_rows]),
        "one_shot_thread_f1": _mean([
            float(((row.get("one_shot_anchor_baseline") or {}).get("metrics") or {}).get("thread_f1") or 0.0)
            for row in case_rows
        ]),
        "one_shot_query_drift_rate": _mean([
            float(((row.get("one_shot_anchor_baseline") or {}).get("metrics") or {}).get("query_drift_rate") or 0.0)
            for row in case_rows
        ]),
    }
    metrics["agentic_loop_thread_f1_lift"] = round(
        float(metrics.get("thread_f1") or 0.0) - float(metrics.get("one_shot_thread_f1") or 0.0),
        4,
    )
    checks = {
        "thread_precision": metrics["thread_precision"] >= float(targets.get("min_thread_precision") or 0.0),
        "thread_recall": metrics["thread_recall"] >= float(targets.get("min_thread_recall") or 0.0),
        "thread_f1": metrics["thread_f1"] >= float(targets.get("min_thread_f1") or 0.0),
        "answerability": metrics["answerability"] >= float(targets.get("min_answerability") or 1.0),
        "query_drift": metrics["query_drift_rate"] <= float(targets.get("max_query_drift_rate") or 0.25),
    }
    flags = BenchmarkShortcutFlags().to_dict()
    judge_statuses = sorted({str(row.get("judge_status") or "") for row in case_rows if str(row.get("judge_status") or "")})
    judge_kinds = sorted({str(row.get("judge_kind") or "") for row in case_rows if str(row.get("judge_kind") or "")})
    return {
        "schema_version": T5_REPORT_SCHEMA,
        "task_id": "t5_thread_fidelity",
        "capability": "C5_thread_fidelity_agentic_loop",
        "case_id": str(fixture.get("id") or "thread_fidelity_fixture"),
        "description": str(fixture.get("description") or ""),
        "generated_from": str(fixture_path or default_fixture_path()),
        "metadata": {
            "runner": "causal_continuity.t5",
            "commit": _repo_commit(),
            "faithfulness": flags,
            "shortcut_flags": flags,
            "notes": [
                "trace_request_seed_plus_causal_expansion",
                "storyline_selection_re_evaluated_against_original_query",
                "deterministic_answerability_proxy_is_default_ci_gate",
            ],
            "ablation_mode": {
                "traversal_enabled": bool(traversal_enabled),
            },
            "judge": {
                "kind": judge_kinds[0] if len(judge_kinds) == 1 else ",".join(judge_kinds),
                "status": judge_statuses[0] if len(judge_statuses) == 1 else ",".join(judge_statuses),
                "is_llm_judge": any(bool(row.get("is_llm_judge")) for row in case_rows),
                "prompt_version": str((case_rows[0] if case_rows else {}).get("prompt_version") or ""),
            },
        },
        "targets": targets,
        "metrics": metrics,
        "checks": checks,
        "pass": all(checks.values()) and all(bool(row.get("pass")) for row in case_rows),
        "cases": sorted(case_rows, key=lambda row: str(row.get("case_id") or "")),
        "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3),
    }


__all__ = [
    "T5_FIXTURE_SCHEMA",
    "T5_REPORT_SCHEMA",
    "default_fixture_path",
    "run_t5_thread_fidelity",
]
