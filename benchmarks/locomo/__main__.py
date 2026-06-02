"""CLI entry point: python -m benchmarks.locomo [options]

Usage examples:

  # Smoke run (first 2 conversations, 5 QA each):
  python -m benchmarks.locomo --corpus path/to/locomo10.json --smoke

  # Full run, write JSON report:
  python -m benchmarks.locomo --corpus path/to/locomo10.json --out report.json

  # Limit conversations and QA per conversation:
  python -m benchmarks.locomo --corpus path/to/locomo10.json --limit 3 --max-qa 10

Corpus:
  Obtain locomo10.json from https://github.com/snap-research/locomo
  The file is not included in this repo due to licensing.

Notes:
  - Category 5 (adversarial/unanswerable) is always excluded — 444/446
    questions have broken answer keys in the public release.
  - Each conversation is evaluated in an isolated temp dir; no state leaks
    between runs.
  - Gold answers and gold evidence dia_ids are never written to the benchmark
    root — they are only consulted in the scoring step after retrieval.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchmarks.locomo.loader import load_locomo_corpus, locomo_samples_to_conversations
from benchmarks.locomo.runner import run_locomo_suite
from benchmarks.locomo.scoring import aggregate_case_scores
from benchmarks.contracts import BenchmarkShortcutFlags


def _render_summary(report: dict) -> str:
    agg = report.get("aggregate") or {}
    overall = agg.get("overall") or {}
    by_cat = agg.get("by_category") or {}
    cfg = report.get("config") or {}
    lines = [
        "LoCoMo Benchmark Results",
        f"  run_at:       {report.get('run_at', '')}",
        f"  git_sha:      {report.get('git_sha', '')}",
        f"  conversations: {cfg.get('conversation_count', 0)}",
        f"  k:             {cfg.get('k', 0)}",
        f"  total_cases:   {agg.get('total_cases', 0)}",
        f"  with_evidence: {agg.get('cases_with_evidence', 0)}",
        "",
        "  Overall (evidence-annotated cases):",
        f"    answer_f1:    {overall.get('answer_f1_mean', 0.0):.4f}",
        f"    recall@1:     {overall.get('recall@1_mean', 0.0):.4f}",
        f"    recall@5:     {overall.get('recall@5_mean', 0.0):.4f}",
        f"    mrr:          {overall.get('mrr_mean', 0.0):.4f}",
        f"    hit_any_rate: {overall.get('hit_any_rate', 0.0):.4f}",
    ]
    if by_cat:
        lines.append("")
        lines.append("  By category:")
        for cat, v in sorted(by_cat.items()):
            lines.append(
                f"    cat{cat}: n={v.get('case_count', 0)}"
                f"  f1={v.get('answer_f1_mean') or 0.0:.4f}"
                f"  recall@5={v.get('recall@5_mean') or 0.0:.4f}"
                f"  mrr={v.get('mrr_mean') or 0.0:.4f}"
            )
    flags = report.get("shortcut_flags") or {}
    if not flags.get("is_faithful"):
        lines.append("")
        lines.append("  WARNING: non-faithful shortcut flags active — results not comparable to official LoCoMo.")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="LoCoMo benchmark runner for Core Memory")
    p.add_argument("--corpus", required=True, help="Path to locomo10.json corpus file")
    p.add_argument("--smoke", action="store_true", help="Smoke run: first 2 conversations, 5 QA each")
    p.add_argument("--limit", type=int, default=None, help="Max conversations to evaluate")
    p.add_argument("--max-qa", type=int, default=None, help="Max QA cases per conversation")
    p.add_argument("--k", type=int, default=10, help="Retrieval k (default 10)")
    p.add_argument("--out", default="", help="Write JSON report to this path")
    p.add_argument("--pretty", action="store_true", help="Pretty-print JSON report to stdout")
    args = p.parse_args()

    corpus_path = Path(args.corpus)
    limit = args.limit
    max_qa = args.max_qa

    if args.smoke:
        limit = limit or 2
        max_qa = max_qa or 5

    print(f"Loading corpus from {corpus_path}...", file=sys.stderr)
    try:
        samples = load_locomo_corpus(corpus_path)
    except Exception as exc:
        print(f"ERROR loading corpus: {exc}", file=sys.stderr)
        return 1

    conversations = locomo_samples_to_conversations(samples, exclude_categories={5})
    print(
        f"Loaded {len(conversations)} conversations, "
        f"{sum(len(c.turns) for c in conversations)} turns, "
        f"{sum(len(c.qa_cases) for c in conversations)} QA cases",
        file=sys.stderr,
    )

    flags = BenchmarkShortcutFlags()
    print("Running evaluation...", file=sys.stderr)
    report = run_locomo_suite(
        conversations,
        shortcut_flags=flags,
        k=args.k,
        max_qa_per_conversation=max_qa,
        limit=limit,
    )

    print(_render_summary(report))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nReport written to {out_path}", file=sys.stderr)

    if args.pretty:
        print(json.dumps(report, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
