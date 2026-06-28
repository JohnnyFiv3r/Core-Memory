# Causal-Continuity Closeout Implementation Plan

Status: closeout plan after evidence-gap hardening.

This document converts the reference evaluation framework into the remaining
implementation sequence. The framework remains the north star; this plan is the
reviewable closeout map for the work that is still needed before the benchmark
package can support a paper-grade claim.

## Current State

| Area | Shipped | Remaining publishable gap |
|---|---|---|
| T1 causal-chain reconstruction | `benchmarks.causal_continuity.t1` strategy matrix with Core Memory full, BM25, similarity-only, dense-vector proxy, executed long-context local proxy, external-adapter rows, and command-adapter execution hooks | Provider-backed long-context or external-memory comparison claims require configured adapter runs and documented external-system configuration |
| T2 calibration reliability | Scored task over effective confidence, Spearman rho, ECE, Brier, and high-band gate in committed report artifact | None for local deterministic evidence |
| T3 temporal state selection | Scored as-of, supersession, and contradiction-surfacing task in committed report artifact | None for local deterministic evidence |
| T4 longitudinal continuity | Scored continuity lift, self-model drift, and goal persistence task in committed report artifact | None for local deterministic evidence |
| T5 thread fidelity | Deterministic trace/storyline proxy with stable ordered top-k, optional supplemental judge hook, and drift metrics | LLM-judge scoring remains optional/supplemental |
| Ablation matrix | `--run-ablation-toggles` disabled-mode fixture runs cover every minimum mechanism row with expected drops observed | Expand beyond deterministic fixtures only if paper scope grows |
| Real-data contrast | Local proxy, LoCoMo/LongMemEval readiness, load-smoke, and bounded evaluation-smoke paths exist | Real corpus evaluation requires user-supplied corpora |
| Reproducibility | Appendix and generated report bundle record exact commands, environment notes, five-run stable repeat check, and source commit | None for local deterministic evidence |
| Evidence manifest | Report carries a machine-readable claim gate separating local deterministic, proxy, configured-adapter, external-corpus, and T5 judge evidence | Provider-backed and real-data public comparison gates remain closed until configured runs/corpora are supplied |

## Publishable Complete

A causal-continuity report is publishable when all of these are true:

- The checked-in synthetic suite runs from a clean checkout with one documented
  command and no network dependency.
- The report carries faithfulness flags for every task, strategy, ablation, and
  contrast row; any false faithfulness flag disqualifies the row from headline
  claims.
- The report carries `evidence_manifest` with `local_fixture_claim_ready=true`
  and provider, real-data leaderboard, and T5 LLM primary claim gates closed
  unless those supplemental runs were explicitly configured.
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

## Completed Local Evidence Sequence

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

Status: complete for local evidence. Long-context/no-memory now executes as a
local proxy by default. Both long-context and external-memory comparator rows
can also execute a configured stdin/stdout command adapter; missing commands
remain explicit `unavailable` rows and command failures remain explicit
`failed` rows.

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

Status: complete for local evidence. The generated report records
`observed_no_expected_drop_rows=0` and `needs_runtime_toggle_rows=0`.

### PR-4: Real-Data Adapter Completion

Goal: finish the ecological-validity contrast path without weakening the
source-agnostic or no-leaderboard-claim rules.

Scope:

- Add a LongMemEval loader implementing `benchmarks.contracts.BenchmarkAdapter`.
- Wire optional external LoCoMo and LongMemEval corpus paths into the contrast
  report.
- Keep corpora out of the repository and fail clearly when paths are missing or
  malformed.
- Run supplied-corpus adapter smokes and bounded evaluation smokes as contrast
  checks, not leaderboard evaluations.

Acceptance:

- Real-data contrast rows can run load smoke and evaluation smoke when external
  corpora are supplied.
- Missing corpora remain honest `dataset_required` or `path_missing` states.
- Local proxy rows still carry `leaderboard_claim: false`.

Status: complete for local evidence. Real corpus execution remains gated by
user-supplied corpus paths and keeps `leaderboard_claim: false`.

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

Implemented appendix: `docs/eval/causal-continuity-reproducibility-appendix.md`.
The committed local report is under `benchmarks/reports/`. The repeat-run check
shows stable headline metrics and stable T5 ordered top-k across five runs.

### PR-6: Evidence Claim Manifest

Goal: make claim readiness machine-readable instead of relying on prose-only
appendix interpretation.

Scope:

- Add `causal_continuity.evidence_manifest.v1` to every suite report.
- Separate local deterministic evidence, proxy comparator rows, configured
  adapter execution, external-corpus evidence, and T5 judge evidence.
- Keep provider-backed, real-data leaderboard, and T5 LLM primary claim gates
  closed unless a future run explicitly supplies the needed configured evidence.

Acceptance:

- Default local reports expose `local_fixture_claim_ready=true`.
- Proxy and unavailable external rows remain visible but do not become public
  comparison claims.
- Configured command adapters are recorded as executed while still requiring
  explicit external-system documentation before public comparison claims.

## Open Decisions

- Which provider-backed long-context and external-memory systems are worth
  running through the command adapter protocol for publication comparisons.
- Whether T5 requires an external LLM judge for a future paper claim, or whether
  deterministic thread metrics remain primary and judge scoring stays secondary.

Defaults for the remaining PRs:

- Do not add external-service requirements to the default local suite.
- Prefer explicit `unavailable` rows over skipped or silently degraded rows.
- Keep public comparison claims separate from local proxy evidence.
- Do not weaken `BenchmarkShortcutFlags` to make a row pass.
