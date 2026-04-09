# Dreamer Contract

Status: Canonical architecture contract

Dreamer is an **offline, non-authoritative, candidate-generating subsystem**.

## What Dreamer is

- asynchronous side-effect work (never required to complete turn writes)
- a source of candidate hypotheses
- reviewable and auditable via candidate queue records

## What Dreamer is not

- a synchronous write-time authority
- a direct truth writer into canonical memory
- a replacement retrieval engine

## Candidate outputs

Dreamer candidates are queued with hypothesis types such as:
- `association_candidate`
- `contradiction_candidate`
- `transferable_lesson_candidate`
- `abstraction_candidate`
- `precedent_candidate`

Each queued row includes:
- source/target bead ids
- hypothesis type
- novelty / grounding / confidence
- rationale
- expected decision impact
- run metadata (run id, mode, session/flush context)

## Scoring direction (structural replay oriented)

Dreamer scoring should prioritize structural signals over lexical similarity alone, including:
- decision → outcome → lesson shape matches
- repeated incident / failure-recovery patterns
- cross-session recurrence
- cross-scope transferability
- contradiction / supersession cues

## Modes

Configured by `CORE_MEMORY_DREAMER_MODE`:
- `off`: no Dreamer processing
- `suggest` (default): generate candidates for review
- `reviewed_apply`: still candidate-first; accepted candidates can be applied only through adjudication

## Adjudication rule

Dreamer does not bypass canonical authority. The expected flow is:
1. Dreamer proposes candidates
2. reviewer/agent accepts or rejects
3. accepted candidates may be applied through canonical store/runtime surfaces
4. rejected candidates remain logged for calibration

## DR-7 behavior eval metrics

Dreamer eval reporting should track behavior-oriented metrics, including:
- repeated mistake reduction proxy
- cross-session transfer success rate
- accepted candidate rate
- downstream retrieval/use rate of accepted outputs
- policy reuse lift proxy

Reference surfaces:
- runtime report: `core_memory.runtime.dreamer_eval.dreamer_eval_report(...)`
- CLI: `core-memory metrics dreamer-eval --since 30d [--strict]`
- eval script: `python -m eval.dreamer_behavior_eval --root <path> --since 30d`

## PV-1 longitudinal benchmark v2 scaffold (proxy telemetry)

Longitudinal benchmark v2 currently reports **candidate-quality proxy telemetry** across:
- no-memory baseline
- summary-only baseline
- core memory without dreamer structural replay
- core memory with dreamer structural replay

Important limitation:
- this is **not** a full strategy replay benchmark yet; the no-memory baseline is a synthetic zero reference and cohorts are derived from candidate/adjudication artifacts.

Reference surfaces:
- runtime report: `core_memory.runtime.longitudinal_benchmark.longitudinal_benchmark_v2(...)`
- CLI: `core-memory metrics longitudinal-benchmark-v2 --since 30d [--strict]`
- eval script: `python -m eval.longitudinal_benchmark_v2 --root <path> --since 30d`

## PV-2 reviewer quick-value path v2

Provide a 5-10 minute walkthrough that demonstrates:
1. one canonical write
2. one retrieval
3. one repeated-incident improvement
4. one Dreamer-assisted transfer improvement

Reference surfaces:
- runtime report: `core_memory.runtime.reviewer_quick_value.reviewer_quick_value_v2(...)`
- CLI: `core-memory metrics reviewer-quick-value-v2 [--strict]`
- eval script: `python -m eval.reviewer_quick_value_v2 --root <path> [--strict]`
