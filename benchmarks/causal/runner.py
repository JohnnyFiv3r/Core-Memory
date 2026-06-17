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

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.passes.association_pass import run_association_pass
from core_memory.association.crawler_contract import merge_crawler_updates
from core_memory.retrieval.agent import recall as core_recall

from .reporting import build_report, render_summary
from .schema import CausalCase, CausalGold, build_cases


def _repo_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
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


def _benchmark_env() -> dict[str, str]:
    return {
        "CORE_MEMORY_CLAIM_LAYER": "1",
        "CORE_MEMORY_CLAIM_EXTRACTION_MODE": "heuristic",
        "CORE_MEMORY_CLAIM_RESOLUTION": "1",
        "CORE_MEMORY_CLAIM_RETRIEVAL_BOOST": "1",
        "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
        "CORE_MEMORY_VECTOR_BACKEND": "local-faiss",
    }


def _materialize_case(root: str, case: CausalCase) -> dict[str, str]:
    """Create beads + causal edges; return key → bead_id map."""
    s = MemoryStore(root)
    key_to_id: dict[str, str] = {}

    for row in case.beads:
        key = str(row.get("key") or "").strip()
        bead_id = s.add_bead(
            type=str(row.get("type") or "context"),
            title=str(row.get("title") or "causal fixture bead"),
            summary=list(row.get("summary") or ["fixture"]),
            detail=str(row.get("detail") or ""),
            session_id=str(row.get("session_id") or "main"),
            source_turn_ids=list(row.get("source_turn_ids") or [f"fx-{key}"]),
            entities=list(row.get("entities") or []),
            topics=list(row.get("topics") or []),
            tags=list(row.get("tags") or []),
        )
        if key:
            key_to_id[key] = bead_id

    # Write the known causal edges as agent-judged associations.
    assoc_rows: list[dict[str, Any]] = []
    for e in case.edges:
        src = key_to_id.get(str(e.get("source_key") or "").strip())
        tgt = key_to_id.get(str(e.get("target_key") or "").strip())
        if not src or not tgt:
            continue
        assoc_rows.append({
            "source_bead_id": src,
            "target_bead_id": tgt,
            "relationship": str(e.get("relationship") or "causes"),
            "confidence": float(e.get("confidence") if e.get("confidence") is not None else 0.9),
            "reason_text": str(e.get("reason_text") or "benchmark causal edge"),
            "provenance": str(e.get("provenance") or "agent_judged"),
        })
    if assoc_rows:
        run_association_pass(
            root=root,
            session_id="main",
            updates={"associations": assoc_rows},
            visible_bead_ids=list(key_to_id.values()),
        )
        merge_crawler_updates(root=root, session_id="main")

    return key_to_id


def _collect_traversed_edges(rca: dict[str, Any]) -> set[tuple[str, str, str]]:
    """Unique traversed edges as (raw_src, raw_dst, relation).

    raw_src/raw_dst preserve the original stored-association direction so the
    identity matches gold edges written as (source_bead, target_bead, rel).
    """
    out: set[tuple[str, str, str]] = set()
    for path in (rca.get("causal_paths") or []):
        if not isinstance(path, dict):
            continue
        for edge in (path.get("edges") or []):
            if not isinstance(edge, dict):
                continue
            src = str(edge.get("raw_src") or edge.get("from") or "").strip()
            dst = str(edge.get("raw_dst") or edge.get("to") or "").strip()
            rel = str(edge.get("relation") or edge.get("rel") or "").strip()
            if src and dst and rel:
                out.add((src, dst, rel))
    return out


def _ranked_root_cause_ids(rca: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """Beads ranked as causal root-cause candidates, best first.

    Prefers the attribution's own root_causes ranking; falls back to evidence
    order when attribution is empty (e.g. traversal produced nothing).
    """
    ranked = [
        str(r.get("bead_id") or "").strip()
        for r in (rca.get("root_causes") or [])
        if isinstance(r, dict) and str(r.get("bead_id") or "").strip()
    ]
    if ranked:
        return ranked
    return [
        str(e.get("bead_id") or "").strip()
        for e in (payload.get("evidence") or [])
        if isinstance(e, dict) and str(e.get("bead_id") or "").strip()
    ]


def _evaluate_case(
    *, case: CausalCase, gold: CausalGold, payload: dict[str, Any], key_to_id: dict[str, str]
) -> dict[str, Any]:
    rca = dict(payload.get("root_cause_attribution") or {})

    # Gold edges in bead-id space.
    gold_edges: set[tuple[str, str, str]] = set()
    for e in case.edges:
        src = key_to_id.get(str(e.get("source_key") or "").strip())
        tgt = key_to_id.get(str(e.get("target_key") or "").strip())
        rel = str(e.get("relationship") or "").strip()
        if src and tgt and rel:
            gold_edges.add((src, tgt, rel))

    traversed = _collect_traversed_edges(rca)
    matched = traversed & gold_edges

    edge_precision = (len(matched) / len(traversed)) if traversed else 0.0
    edge_recall = (len(matched) / len(gold_edges)) if gold_edges else 0.0
    edge_f1 = (
        (2 * edge_precision * edge_recall / (edge_precision + edge_recall))
        if (edge_precision + edge_recall) > 0
        else 0.0
    )

    gold_root_id = key_to_id.get(gold.gold_root_cause_key, "")
    distractor_ids = {key_to_id.get(k, "") for k in case.distractor_keys}
    distractor_ids.discard("")

    ranked = _ranked_root_cause_ids(rca, payload)
    ranked_set = set(ranked)

    # root_cause_correct: top-ranked causal candidate is the gold root cause.
    root_cause_correct = bool(ranked and ranked[0] == gold_root_id)

    # Attribution depth: deepest causal path traversed.
    attribution_depth = 0
    for path in (rca.get("causal_paths") or []):
        if isinstance(path, dict):
            attribution_depth = max(attribution_depth, int(path.get("depth") or 0))

    # Causal grounding: traversal reconstructed a path reaching the gold root
    # cause via at least one causal (non-temporal) edge.
    gold_root_reached = bool(gold_root_id and gold_root_id in _reachable_via_paths(rca))
    grounding_full = bool(gold_root_reached and edge_recall >= 1.0)

    # Distractor survival (headline): the gold root cause outranks every
    # adversarial distractor in the causal ranking. Vacuously survives when no
    # distractors are configured.
    distractor_survived = _distractor_survival(ranked, gold_root_id, distractor_ids)

    checks = {
        "edge_recall_complete": edge_recall >= 1.0,
        "root_cause_correct": root_cause_correct,
        "grounding_full": grounding_full,
        "distractor_survived": distractor_survived,
    }
    overall = all(checks.values())

    return {
        "edge_precision": round(edge_precision, 4),
        "edge_recall": round(edge_recall, 4),
        "edge_f1": round(edge_f1, 4),
        "traversed_edge_count": int(len(traversed)),
        "gold_edge_count": int(len(gold_edges)),
        "matched_edge_count": int(len(matched)),
        "attribution_depth": int(attribution_depth),
        "root_cause_correct": bool(root_cause_correct),
        "grounding_full": bool(grounding_full),
        "gold_root_reached": bool(gold_root_reached),
        "distractor_survived": bool(distractor_survived),
        "distractor_count": int(len(distractor_ids)),
        "ranked_root_cause_ids": list(ranked[:8]),
        "gold_root_cause_id": gold_root_id,
        "gold_root_in_ranking": bool(gold_root_id in ranked_set),
        "checks": checks,
        "pass": bool(overall),
    }


def _reachable_via_paths(rca: dict[str, Any]) -> set[str]:
    """All bead IDs appearing as nodes in any traversed causal path."""
    out: set[str] = set()
    for path in (rca.get("causal_paths") or []):
        if not isinstance(path, dict):
            continue
        for nid in (path.get("nodes") or []):
            s = str(nid or "").strip()
            if s:
                out.add(s)
    return out


def _distractor_survival(ranked: list[str], gold_root_id: str, distractor_ids: set[str]) -> bool:
    """True when the gold root cause outranks every distractor.

    The adversarial premise: distractors are the semantically closest beads, so
    a pure-similarity system would rank them at the top. Causal traversal
    "survives" when the true root cause appears and precedes all distractors.
    """
    if not distractor_ids:
        return True  # nothing to beat
    if not gold_root_id or gold_root_id not in ranked:
        return False
    gold_pos = ranked.index(gold_root_id)
    for did in distractor_ids:
        if did in ranked and ranked.index(did) < gold_pos:
            return False
    return True


def run_case(*, case: CausalCase, gold: CausalGold, benchmark_root: str | None = None) -> dict[str, Any]:
    t0 = time.perf_counter()

    def _run(root: str) -> dict[str, Any]:
        with _env_overrides(_benchmark_env()):
            t_setup = time.perf_counter()
            key_to_id = _materialize_case(root, case)
            setup_ms = (time.perf_counter() - t_setup) * 1000.0

            req = {"raw_query": case.query, "intent": case.intent, "k": int(case.k)}
            t_query = time.perf_counter()
            result = core_recall(req, effort="high", root=root, explain=True, include_raw=True)
            retrieval_ms = (time.perf_counter() - t_query) * 1000.0
            payload = result.to_dict()

        metrics = _evaluate_case(case=case, gold=gold, payload=payload, key_to_id=key_to_id)
        return {
            "case_id": case.id,
            "bucket_labels": list(case.bucket_labels),
            "query": case.query,
            "expected_grounding": gold.expected_grounding,
            "write_setup_ms": round(setup_ms, 3),
            "retrieval_ms": round(retrieval_ms, 3),
            "tier_path": list(payload.get("tier_path") or []),
            "warnings": list(payload.get("warnings") or []),
            **metrics,
        }

    if benchmark_root:
        Path(benchmark_root).mkdir(parents=True, exist_ok=True)
        row = _run(str(benchmark_root))
    else:
        with tempfile.TemporaryDirectory(prefix="cm-causal-bench-") as td:
            row = _run(td)

    row["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
    return row


def run_benchmark(
    *,
    fixtures_dir: Path,
    gold_dir: Path,
    subset: str = "full",
    limit: int | None = None,
) -> dict[str, Any]:
    pairs = build_cases(fixtures_dir=fixtures_dir, gold_dir=gold_dir)
    pairs = sorted(pairs, key=lambda p: p[0].id)

    if subset == "local":
        pairs = pairs[: min(len(pairs), 4)]
    if limit is not None:
        pairs = pairs[: max(1, int(limit))]

    case_results = [run_case(case=case, gold=gold) for case, gold in pairs]

    metadata = {
        "runner": "causal",
        "subset": subset,
        "case_count": len(case_results),
        "commit": _repo_commit(),
        "notes": ["synthetic_causal_chains", "adversarial_distractors", "deterministic_local_replay"],
    }
    return build_report(metadata=metadata, case_results=case_results)


def main() -> int:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="Run the causal-chain reconstruction benchmark")
    p.add_argument("--fixtures", default=str(here / "fixtures"))
    p.add_argument("--gold", default=str(here / "gold"))
    p.add_argument("--subset", choices=["local", "full"], default="full")
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
