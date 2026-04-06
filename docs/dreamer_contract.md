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
