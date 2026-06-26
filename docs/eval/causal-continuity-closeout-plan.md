# Causal-Continuity Closeout Implementation Plan

Status: PR-1 closeout plan after `#288`.

This document converts the reference evaluation framework into the remaining
implementation sequence. The framework remains the north star; this plan is the
reviewable closeout map for the work that is still needed before the benchmark
package can support a paper-grade claim.

## Current State

| Area | Shipped | Remaining publishable gap |
|---|---|---|
| T1 causal-chain reconstruction | `benchmarks.causal_continuity.t1` strategy matrix with Core Memory full, BM25, similarity-only, dense-vector proxy, long-context/no-memory, and external-adapter rows | Replace unavailable/proxy comparator rows with executed adapters when publishable runs are available |
| T2 calibration reliability | Scored task over effective confidence, Spearman rho, ECE, Brier, and high-band gate | Include in committed report artifact and repeat-run evidence |
| T3 temporal state selection | Scored as-of, supersession, and contradiction-surfacing task | Include in committed report artifact and repeat-run evidence |
| T4 longitudinal continuity | Scored continuity lift, self-model drift, and goal persistence task | Include in committed report artifact and repeat-run evidence |
| T5 thread fidelity | Deterministic trace/storyline proxy with precision, recall, answerability, and drift metrics | Decide whether an external LLM judge is needed for the paper claim; otherwise label deterministic answerability as the supported local claim |
| Ablation matrix | Optional `ablation_matrix` attachment plus `--run-ablation-toggles` disabled-mode fixture runs | Expand runtime toggles beyond the deterministic local fixture set if needed for paper evidence |
| Real-data contrast | Optional `real_data_contrast` attachment with local proxy, LoCoMo readiness, and LongMemEval status | Add LongMemEval loader and run external-corpus paths when data is supplied |
| Reproducibility | Runner commands exist | Commit appendix plus generated report bundle with exact commands, environment notes, determinism checks, and source commit |

## Publishable Complete

A causal-continuity report is publishable when all of these are true:

- The checked-in synthetic suite runs from a clean checkout with one documented
  command and no network dependency.
- The report carries faithfulness flags for every task, strategy, ablation, and
  contrast row; any false faithfulness flag disqualifies the row from headline
  claims.
- T1 includes Core Memory full, BM25, similarity/dense retrieval, and
  long-context/no-memory comparator rows. External memory comparator rows may be
  marked unavailable, but public comparison claims require actual runs.
- The ablation matrix has no `needs_runtime_toggle` rows for the minimum
  publishable mechanism claims: causal traversal, myelination backpressure,
  validated-outcome reward, Dreamer continuity, supersession/temporal filtering,
  and the agentic recall loop.
- Real-data rows are always labeled as contrast conditions. Local proxy results
  never become LoCoMo or LongMemEval leaderboard claims.
- A reproducibility appendix records the exact command, commit, Python version,
  dependency/degradation notes, generated report path, repeated-run determinism
  result, and known unavailable external resources.

## Remaining PR Sequence

### PR-2: Baseline Completion

Goal: make the T1 comparison set explicit enough for paper tables.

Scope:

- Add stable comparator rows for dense-vector retrieval, long-context/no-memory,
  and external memory adapter baselines.
- Keep CI/local operation deterministic: unavailable credentials or backends
  should produce `status: unavailable` with a reason, not a silent green result.
- Preserve current BM25 and similarity-only rows as local baselines.
- Extend report summaries so baseline availability and headline metrics are
  visible without opening the full JSON.

Acceptance:

- The T1 matrix contains stable row IDs for the required comparator set.
- Dense/vector and long-context rows cannot accidentally claim causal traversal.
- External adapter rows do not make product or leaderboard claims without an
  actual adapter run.
- Existing T1 tests stay green and new tests cover unavailable-status behavior.

### PR-3: True Ablation Runs

Goal: turn the current ablation attachment from a coverage inventory into
mechanism-specific runs.

Scope:

- Add a heavier opt-in mode, separate from `--include-ablations`, that executes
  supported runtime-disabled configurations.
- Cover causal traversal off, myelination bonus off, validated-outcome reward
  off, Dreamer off, supersession/temporal filter off, and one-shot recall loop.
- Keep proxy rows only where there is a documented reason no runtime toggle
  exists yet.

Acceptance:

- The ablation matrix distinguishes `observed`, `observed_no_expected_drop`,
  `unavailable`, and `failed` without conflating them.
- Minimum publishable mechanism rows no longer report `needs_runtime_toggle`.
- Faithfulness flags remain clean for every headline ablation row.

### PR-4: Real-Data Adapter Completion

Goal: finish the ecological-validity contrast path without weakening the
source-agnostic or no-leaderboard-claim rules.

Scope:

- Add a LongMemEval loader implementing `benchmarks.contracts.BenchmarkAdapter`.
- Wire optional external LoCoMo and LongMemEval corpus paths into the contrast
  report.
- Keep corpora out of the repository and fail clearly when paths are missing or
  malformed.

Acceptance:

- Real-data contrast rows can run when external corpora are supplied.
- Missing corpora remain honest `dataset_required` or `path_missing` states.
- Local proxy rows still carry `leaderboard_claim: false`.

### PR-5: Reproducibility Appendix

Goal: commit the evidence bundle that reviewers can rerun.

Scope:

- Add a reproducibility appendix under `docs/eval/`.
- Commit a current generated JSON report under `benchmarks/reports/` if the
  repository policy allows generated benchmark artifacts.
- Record exact runner commands for full suite, ablations, real-data contrast,
  and any unavailable external-resource rows.
- Add a deterministic repeat-run check for ordered top-k outputs where the
  harness exposes ranked results.

Acceptance:

- A reviewer can reproduce the committed report from the documented commands.
- The appendix names dependency degradation explicitly, such as lexical fallback
  when semantic/vector backends are unavailable.
- The final PRD checklist separates completed harness work from remaining
  evidence limitations.

## Open Decisions

- Whether the long-context/no-memory comparator should be an LLM-backed baseline
  only, or whether a deterministic local proxy is acceptable for CI.
- Which external memory adapters are worth implementing versus reporting as
  optional comparison slots.
- Whether T5 requires an external LLM judge for the minimum publishable claim, or
  whether deterministic thread metrics are the primary claim and judge scoring is
  explicitly secondary.

Defaults for the remaining PRs:

- Do not add external-service requirements to the default local suite.
- Prefer explicit `unavailable` rows over skipped or silently degraded rows.
- Keep public comparison claims separate from local proxy evidence.
- Do not weaken `BenchmarkShortcutFlags` to make a row pass.
