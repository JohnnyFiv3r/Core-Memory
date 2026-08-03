# Observation Ledger Architecture Contract

- **Status:** Enforced foundation
- **Contract version:** 1.0
- **Guard schema:** `core_memory.architecture_guards.v2`
- **Governing PRD:** `../PRD/observation-ledger-architecture.md`
- **Execution plan:** `../PRD/execution-plan-observation-ledger-architecture.md`
- **Stage 0 plan:** `../PRD/execution-plan-observation-ledger-stage-0.md`
- **Verified implementation base:** `0257cf11e23ce2ec7fd08590e10df00d67402d20`

## Purpose

This contract defines the architectural truths that Core Memory must preserve
while it moves from legacy files, feature-specific state machines, and
deterministic semantic fallbacks to the Observation Ledger. It is enforced by
the existing architecture guard; it does not create a second policy engine.

The guard is a ratchet. Existing debt is explicit, exact, owned, fingerprinted,
and assigned to a deletion PR. Removing debt is always allowed. Adding a new
authority, fallback, occurrence, wildcard exception, or unowned exception is a
CI failure.

## Enforced invariants

| ID | Invariant |
|---|---|
| `CM-OBS-001` | Every bead is an evidence-bound annotation of an observed user-agent event. |
| `CM-SEM-001` | An LLM authors meaning-bearing fields and conditional semantic decisions. |
| `CM-SEM-002` | Missing, invalid, or exhausted semantic execution writes no manufactured fallback meaning. |
| `CM-CLAIM-001` | Current truth follows explicit revision chains; unresolved incompatible claims remain ambiguous. |
| `CM-ASSOC-001` | Predicate, direction, time, and causality are evidence-bound LLM decisions. |
| `CM-ARTIFACT-001` | Goals, storylines, Dreamer findings, SOUL entries, and other syntheses share one evidence-bound append-only artifact lifecycle. |
| `CM-PROMOTION-001` | Promotion changes hot-context representation only; it cannot raise truth or remove archive evidence. |
| `CM-RETRIEVAL-001` | Retrieval uses one evidence-hydrating pipeline and returns supported citations or abstains. |
| `CM-LEDGER-001` | Canonical semantic state is append-only and has one authority. |
| `CM-BOUNDARY-001` | Integrations translate observed events and never invent semantic authority. |
| `CM-BENCH-001` | Gold leakage, direct semantic preloading, or benchmark shortcuts disqualify quality evidence. |

## Observation and semantic authority

A bead documents a bounded event. The LLM acts as a constrained note-taker:
it selects a label from the versioned vocabulary, writes a faithful title and
summary, cites supporting evidence, and abstains from unsupported assertions.

Assertions, associations, revisions, and artifacts are optional. A meaningful
event may produce several supported assertions. A greeting or operational
acknowledgement may produce a thin bead with none. Schema pressure must never
turn absence of meaning into invented content.

Deterministic code may:

- authenticate callers and enforce tenant boundaries;
- assign IDs, source identity, system time, and ledger positions;
- validate schemas, evidence spans, citations, vocabulary, and referential integrity;
- collect relationship-neutral candidates;
- execute search, graph traversal, temporal math, token packing, and hydration;
- append transactions, schedule jobs, retry operations, and rebuild projections.

Deterministic code may not author or choose:

- bead labels, titles, summaries, or assertions;
- claim meaning or revision action;
- association predicate, direction, validity, or causal meaning;
- goals, storylines, Dreamer findings, identity, values, tensions, or SOUL prose;
- relative context value or promotion rank;
- retrieval intent, semantic relevance, sufficiency, answer prose, or abstention.

## Current truth

Current truth is a deterministic resolution of LLM-authored, evidence-bound
revision decisions. The resolver follows explicit revision links and validates
their mechanics. It does not infer semantic compatibility from timestamps.

Recency, repetition, retrieval frequency, salience, promotion, and
myelination cannot increase factual authority. An incompatible unresolved set
returns ambiguity. There is no implicit latest-wins rule.

## Failure contract

Semantic attempts may complete, retry, fail, or remain pending. They do not
fall back to deterministic content. Provider unavailability, invalid output,
failed evidence validation, and exhausted retries create receipts and
operational state only. They create no canonical semantic rows.

## Exception governance

The canonical registry is
`scripts/architecture_guards_baseline.json`. Every exception contains:

- a stable ID and invariant IDs;
- one supported category;
- an exact repo-relative path and symbol;
- a content-derived fingerprint;
- narrowly allowed and explicitly forbidden behavior;
- justification and provenance requirements;
- an owner, deletion PR, and maximum occurrence count.

Supported categories are:

- `canonical_file_authority`
- `deterministic_semantic_author`
- `semantic_fallback`
- `duplicate_resolver`
- `feature_store`
- `legacy_queue`
- `boundary_import`
- `benchmark_shortcut`

Paths cannot be absolute, contain parent traversal, or contain glob syntax.
Deletion targets must be later program phases. Exception occurrence ceilings
may decrease but may not increase.

The registry records the source commit used to establish the debt snapshot.
Changing exception metadata changes its fingerprint and requires explicit
review. A baseline-generation command can emit only a candidate diff to a
separate file; it cannot overwrite or promote the canonical registry.

## Enforcement

Read-only report:

```bash
python scripts/check_architecture_guards.py --report
```

Required ratchet:

```bash
python scripts/check_architecture_guards.py \
  --baseline scripts/architecture_guards_baseline.json \
  --fail-on-new
```

Review-only candidate:

```bash
python scripts/check_architecture_guards.py \
  --write-baseline-candidate /tmp/architecture-guard-candidate.json
```

The candidate contains new and resolved violation IDs. It is never adopted
automatically. The compatibility-surface ratchet remains part of the same
workflow and retains its existing baseline.

## PR-00A boundary

PR-00A establishes and enforces this contract. It deliberately does not alter
runtime semantic behavior, migrate authority, create the ledger, or claim that
legacy quality is acceptable. Those changes begin only after their declared
Stage 0 prerequisites merge.
