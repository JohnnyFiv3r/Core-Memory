"""LoCoMo lifecycle runner.

Lifecycle per conversation:
  1. ingest_conversation() — replay all turns via process_turn_finalized
  2. run_async_jobs()      — drain semantic index build + compaction
  3. per QA case:
       a. recall()          — three-tier retrieval (lexical → semantic → causal)
       b. map bead_ids → dia_ids using the map built at ingest time
       c. score_answer()    — category-aware token F1 / multihop F1
       d. compute_evidence_recall() — recall@k, MRR, hit_any in dia_id space

Contamination guards:
  - Gold answers are never written to or read from the benchmark root.
  - Gold evidence (dia_ids) is only consulted in the scoring step, after
    recall() has already returned its results.
  - k passed to recall() is a fixed constant, not derived from len(gold_evidence).
  - Each conversation gets an isolated temp dir; no state leaks across runs.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.retrieval.agent import recall as core_recall
from core_memory.retrieval.semantic_index import semantic_doctor
from core_memory.runtime.queue.jobs import run_async_jobs

from benchmarks.contracts import BenchmarkConversation, BenchmarkQA, BenchmarkShortcutFlags
from benchmarks.locomo.ingest import ingest_conversation
from benchmarks.locomo.scoring import aggregate_case_scores, compute_evidence_recall, score_answer

_DEFAULT_K = 10
_RETRIEVAL_EFFORT = "high"
_OFFICIAL_CATEGORIES = {1, 2, 3, 4}


def _repo_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=8", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


@contextmanager
def _env_overrides(extra: dict[str, str]):
    saved = {k: os.environ.get(k) for k in extra}
    try:
        for k, v in extra.items():
            os.environ[k] = v
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def _retrieval_env() -> dict[str, str]:
    return {
        "CORE_MEMORY_CLAIM_LAYER": "1",
        "CORE_MEMORY_CLAIM_EXTRACTION_MODE": "heuristic",
        "CORE_MEMORY_CLAIM_RESOLUTION": "1",
        "CORE_MEMORY_CLAIM_RETRIEVAL_BOOST": "1",
        "CORE_MEMORY_PREVIEW_ASSOC_PROMOTION": "1",
        "CORE_MEMORY_PREVIEW_ASSOC_ALLOW_SHARED_TAG": "1",
        "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
    }


def _extract_dia_ids_from_evidence(
    evidence: list[Any],
    bead_to_dia: dict[str, str],
) -> list[str]:
    """
    Convert a ranked list of EvidenceItem objects to dia_ids using the
    bead→dia_id map built at ingest time. Items without a known mapping
    are dropped (they could not have come from the replayed transcript).
    """
    out: list[str] = []
    for item in evidence:
        bead_id = str(getattr(item, "bead_id", None) or "").strip()
        if not bead_id:
            # EvidenceItem might be a dict in some code paths
            if isinstance(item, dict):
                bead_id = str(item.get("bead_id") or "").strip()
        if bead_id and bead_id in bead_to_dia:
            dia_id = bead_to_dia[bead_id]
            if dia_id not in out:
                out.append(dia_id)
    return out


def run_qa_case(
    *,
    root: str,
    qa: BenchmarkQA,
    bead_to_dia: dict[str, str],
    k: int = _DEFAULT_K,
) -> dict[str, Any]:
    """
    Run a single QA case against the pre-ingested benchmark root.

    CRITICAL: qa.expected_answer and qa.gold_evidence must NEVER be
    written into root or passed to recall(). They are only consulted here
    in the scoring step, after recall() has already returned results.
    """
    t0 = time.perf_counter()

    with _env_overrides(_retrieval_env()):
        result = core_recall(
            qa.question,
            effort=_RETRIEVAL_EFFORT,
            root=root,
            k=k,
            explain=True,
            include_raw=True,
        )

    latency_ms = (time.perf_counter() - t0) * 1000.0

    # Map retrieved bead_ids → dia_ids — only bead→dia pairs from this
    # conversation's ingest pass are in bead_to_dia, so foreign beads are
    # naturally filtered out.
    evidence_list = list(result.evidence or [])
    retrieved_dia_ids = _extract_dia_ids_from_evidence(evidence_list, bead_to_dia)

    cat = int(qa.category or 0) if str(qa.category or "").isdigit() else 0
    excluded = cat not in _OFFICIAL_CATEGORIES

    # Evidence scoring — dia_id space, not bead_id space
    evidence_recall = compute_evidence_recall(
        gold_evidence=list(qa.gold_evidence or []),
        retrieved=retrieved_dia_ids,
        ks=[1, 3, 5, 10],
    )

    # Answer scoring — extractive: use top result content as prediction.
    # Gold answer is only accessed here, never earlier.
    prediction = ""
    if evidence_list:
        first = evidence_list[0]
        # EvidenceItem has content_excerpt + title; prefer content_excerpt
        excerpt = str(getattr(first, "content_excerpt", None) or "").strip()
        title = str(getattr(first, "title", None) or "").strip()
        prediction = excerpt or title

    answer_f1 = 0.0
    if not excluded and qa.expected_answer is not None:
        answer_f1 = score_answer(
            category=cat,
            prediction=prediction,
            answer=qa.expected_answer,
        )

    answer_outcome = str(getattr(result, "status", None) or result.to_dict().get("answer_outcome") or "")

    return {
        "qa_id": qa.qa_id,
        "question": qa.question,
        "category": str(cat),
        "excluded": excluded,
        "retrieved_dia_ids": retrieved_dia_ids[:k],
        "retrieved_bead_count": len(evidence_list),
        "evidence_recall": evidence_recall,
        "answer_f1": round(answer_f1, 4),
        "answer_outcome": answer_outcome,
        "prediction_snippet": prediction[:200],
        "latency_ms": round(latency_ms, 3),
        "bucket_labels": list(qa.bucket_labels),
    }


def run_conversation(
    conversation: BenchmarkConversation,
    *,
    shortcut_flags: BenchmarkShortcutFlags | None = None,
    k: int = _DEFAULT_K,
    max_qa: int | None = None,
    root: str | None = None,
    keep_root: bool = False,
) -> dict[str, Any]:
    """
    Full lifecycle for one BenchmarkConversation.

    root:      If given, use this directory (must be empty). Caller is
               responsible for cleanup.
    keep_root: If True and root is auto-created, skip cleanup (for debugging).

    Returns a dict with per-case scores and aggregate metrics.
    """
    if shortcut_flags is None:
        shortcut_flags = BenchmarkShortcutFlags()

    t0_total = time.perf_counter()
    own_dir = root is None
    td: str

    if own_dir:
        td = tempfile.mkdtemp(prefix="cm-locomo-")
    else:
        td = str(root)
        Path(td).mkdir(parents=True, exist_ok=True)

    try:
        # Phase 1: ingest — replay turns, build dia→bead map
        t_ingest = time.perf_counter()
        dia_to_bead = ingest_conversation(td, conversation, shortcut_flags=shortcut_flags)
        ingest_ms = (time.perf_counter() - t_ingest) * 1000.0

        # Phase 2: drain async jobs (semantic index, compaction)
        t_drain = time.perf_counter()
        drain_result = run_async_jobs(root=td, run_semantic=True, max_compaction=50, max_side_effects=50)
        drain_ms = (time.perf_counter() - t_drain) * 1000.0

        semantic_diag = semantic_doctor(Path(td))

        # Invert dia→bead for retrieval result mapping
        bead_to_dia = {v: k for k, v in dia_to_bead.items()}

        # Phase 3: QA evaluation
        qa_cases = list(conversation.qa_cases)
        if max_qa is not None:
            qa_cases = qa_cases[:max_qa]

        case_scores: list[dict[str, Any]] = []
        for qa in qa_cases:
            case_result = run_qa_case(root=td, qa=qa, bead_to_dia=bead_to_dia, k=k)
            case_scores.append(case_result)

        total_ms = (time.perf_counter() - t0_total) * 1000.0
        aggregated = aggregate_case_scores(case_scores)

        return {
            "conversation_id": conversation.conversation_id,
            "session_id": conversation.session_id,
            "turn_count": len(conversation.turns),
            "qa_count": len(qa_cases),
            "dia_bead_map_size": len(dia_to_bead),
            "shortcut_flags": shortcut_flags.to_dict(),
            "timing_ms": {
                "ingest": round(ingest_ms, 3),
                "drain": round(drain_ms, 3),
                "total": round(total_ms, 3),
            },
            "semantic_backend": semantic_diag,
            "aggregate": aggregated,
            "cases": case_scores,
        }

    finally:
        if own_dir and not keep_root:
            shutil.rmtree(td, ignore_errors=True)


def run_locomo_suite(
    conversations: list[BenchmarkConversation],
    *,
    shortcut_flags: BenchmarkShortcutFlags | None = None,
    k: int = _DEFAULT_K,
    max_qa_per_conversation: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """
    Run all conversations and produce a top-level report.

    Each conversation gets an isolated temp dir — state cannot leak across runs.
    """
    if shortcut_flags is None:
        shortcut_flags = BenchmarkShortcutFlags()

    if not shortcut_flags.is_faithful():
        raise ValueError(
            f"run_locomo_suite: non-faithful shortcut flags passed: {shortcut_flags.to_dict()}\n"
            "Official evaluation requires is_faithful()=True."
        )

    if limit is not None:
        conversations = conversations[:limit]

    t0 = time.perf_counter()
    conversation_results: list[dict[str, Any]] = []
    all_case_scores: list[dict[str, Any]] = []

    for conv in conversations:
        result = run_conversation(
            conv,
            shortcut_flags=shortcut_flags,
            k=k,
            max_qa=max_qa_per_conversation,
        )
        conversation_results.append(result)
        all_case_scores.extend(result.get("cases") or [])

    total_ms = (time.perf_counter() - t0) * 1000.0
    overall_agg = aggregate_case_scores(all_case_scores)

    return {
        "schema_version": "locomo_runner.v1",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _repo_sha(),
        "backend": "JsonFileBackend",
        "shortcut_flags": shortcut_flags.to_dict(),
        "config": {
            "k": k,
            "retrieval_effort": _RETRIEVAL_EFFORT,
            "max_qa_per_conversation": max_qa_per_conversation,
            "conversation_count": len(conversations),
            "excluded_categories": [5],
        },
        "timing_ms": {"total": round(total_ms, 3)},
        "aggregate": overall_agg,
        "conversations": conversation_results,
    }
