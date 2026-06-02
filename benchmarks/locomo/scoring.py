"""LoCoMo category-aware answer scoring and evidence recall.

Category map:
  1 = multi-hop (comma-separated sub-answers, use multihop_f1)
  2 = single-hop temporal (token_f1)
  3 = temporal with semicolons (token_f1 on prefix before first semicolon)
  4 = factual (token_f1)
  5 = adversarial/unanswerable — EXCLUDED from all official evaluation
      (444/446 questions have broken answer keys in the public corpus)

Evidence is scored in dia_id space, never bead_id space, because bead IDs
are non-deterministic across ingestion runs.
"""
from __future__ import annotations

import re
import string
from collections import Counter
from typing import Any


_OFFICIAL_CATEGORIES = {1, 2, 3, 4}
_ARTICLES = {"a", "an", "the"}


def normalize_text(value: str) -> str:
    text = str(value or "").lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    tokens = [t for t in text.split() if t not in _ARTICLES]
    return " ".join(tokens)


def token_f1(prediction: str, answer: str) -> float:
    pred_tokens = normalize_text(prediction).split()
    gold_tokens = normalize_text(answer).split()
    if not pred_tokens and not gold_tokens:
        return 1.0
    if not pred_tokens or not gold_tokens:
        return 0.0
    common = Counter(pred_tokens) & Counter(gold_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(gold_tokens)
    return (2 * precision * recall) / (precision + recall)


def multihop_f1(prediction: str, answer: str) -> float:
    """Category 1: gold answer may be comma-separated; score max F1 per sub-answer."""
    sub_answers = [a.strip() for a in str(answer or "").split(",") if a.strip()]
    if not sub_answers:
        return token_f1(prediction, answer)
    return max(token_f1(prediction, sub) for sub in sub_answers)


def score_answer(*, category: int | str | None, prediction: str, answer: str) -> float:
    """Category-aware answer scoring. Returns 0.0–1.0."""
    cat = int(category) if str(category or "").isdigit() else 0
    if cat == 1:
        return multihop_f1(prediction, answer)
    if cat == 3:
        # Strip from first semicolon — the gold answer prefix is the canonical answer
        gold = str(answer or "").split(";", 1)[0].strip()
        return token_f1(prediction, gold)
    if cat in {2, 4}:
        return token_f1(prediction, answer)
    if cat == 5:
        # Category 5 is excluded from official evaluation; return neutral 1.0
        # so it doesn't drag aggregate metrics, but flag it in metadata.
        return 1.0
    # Unknown category falls back to token_f1
    return token_f1(prediction, answer)


def compute_evidence_recall(
    *,
    gold_evidence: list[str],
    retrieved: list[str],
    ks: list[int] | None = None,
) -> dict[str, Any]:
    """
    Score evidence recall in dia_id space.

    gold_evidence: list of dia_ids that are gold evidence (e.g. ["D1:3", "D1:7"])
    retrieved:     list of dia_ids in rank order (position 0 = rank 1)
    ks:            list of k values to compute recall@k for (default [1, 3, 5, 10])

    Returns:
        recall@k for each k, MRR, hit_any, vacuous flag.

    When gold_evidence is empty, recall@k = 1.0 (vacuous recall). These cases
    are excluded from aggregate metrics when comparing to published systems.
    """
    if ks is None:
        ks = [1, 3, 5, 10]

    gold_set = set(str(d).strip() for d in gold_evidence if str(d).strip())

    if not gold_set:
        return {
            "vacuous": True,
            **{f"recall@{k}": 1.0 for k in ks},
            "mrr": 1.0,
            "hit_any": True,
        }

    retrieved_normalized = [str(d).strip() for d in retrieved]
    hit_ranks: list[int] = []
    for rank, dia_id in enumerate(retrieved_normalized, start=1):
        if dia_id in gold_set:
            hit_ranks.append(rank)

    result: dict[str, Any] = {"vacuous": False}
    for k in ks:
        hits_at_k = sum(1 for r in hit_ranks if r <= k)
        result[f"recall@{k}"] = round(hits_at_k / len(gold_set), 4)

    mrr = 0.0
    if hit_ranks:
        mrr = 1.0 / min(hit_ranks)
    result["mrr"] = round(mrr, 4)
    result["hit_any"] = bool(hit_ranks)
    return result


def aggregate_case_scores(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate per-case scores. Separates vacuous-recall cases from annotated ones.
    Primary metrics are computed on cases_with_evidence only, to match published
    LoCoMo evaluation methodology.
    """
    with_evidence = [c for c in cases if not bool(c.get("evidence_recall", {}).get("vacuous", True))]
    without_evidence = [c for c in cases if bool(c.get("evidence_recall", {}).get("vacuous", True))]

    def _mean_key(rows: list[dict[str, Any]], key: str) -> float | None:
        vals = [float(r[key]) for r in rows if key in r and r[key] is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    answer_f1_all = [float(c.get("answer_f1", 0.0)) for c in cases if not c.get("excluded")]
    answer_f1_with_ev = [float(c.get("answer_f1", 0.0)) for c in with_evidence if not c.get("excluded")]

    by_category: dict[str, dict[str, Any]] = {}
    for case in cases:
        cat = str(case.get("category") or "unknown")
        row = by_category.setdefault(cat, {"cases": [], "answer_f1": [], "recall@1": [], "recall@5": [], "mrr": []})
        row["cases"].append(case.get("qa_id") or case.get("id"))
        if not case.get("excluded"):
            row["answer_f1"].append(float(case.get("answer_f1", 0.0)))
        ev = dict(case.get("evidence_recall") or {})
        if not ev.get("vacuous"):
            row["recall@1"].append(float(ev.get("recall@1", 0.0)))
            row["recall@5"].append(float(ev.get("recall@5", 0.0)))
            row["mrr"].append(float(ev.get("mrr", 0.0)))

    def _agg(nums: list[float]) -> float | None:
        return round(sum(nums) / len(nums), 4) if nums else None

    by_category_agg = {
        cat: {
            "case_count": len(v["cases"]),
            "answer_f1_mean": _agg(v["answer_f1"]),
            "recall@1_mean": _agg(v["recall@1"]),
            "recall@5_mean": _agg(v["recall@5"]),
            "mrr_mean": _agg(v["mrr"]),
        }
        for cat, v in sorted(by_category.items())
    }

    return {
        "total_cases": len(cases),
        "cases_with_evidence": len(with_evidence),
        "cases_without_evidence_annotation": len(without_evidence),
        "overall": {
            "answer_f1_mean": _agg(answer_f1_all),
            "answer_f1_mean_with_evidence": _agg(answer_f1_with_ev),
            "recall@1_mean": _mean_key(with_evidence, "evidence_recall.recall@1") if False else _agg(
                [float(c.get("evidence_recall", {}).get("recall@1", 0.0)) for c in with_evidence]
            ),
            "recall@5_mean": _agg(
                [float(c.get("evidence_recall", {}).get("recall@5", 0.0)) for c in with_evidence]
            ),
            "mrr_mean": _agg(
                [float(c.get("evidence_recall", {}).get("mrr", 0.0)) for c in with_evidence]
            ),
            "hit_any_rate": _agg(
                [1.0 if c.get("evidence_recall", {}).get("hit_any") else 0.0 for c in with_evidence]
            ),
        },
        "by_category": by_category_agg,
        "methodology_note": "primary_recall_metrics_exclude_vacuous_evidence_cases",
    }
