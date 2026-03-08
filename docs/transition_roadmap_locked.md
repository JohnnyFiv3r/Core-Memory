# Core Memory Transition Roadmap (Locked)

Status: Canonical transition reference
Audience: Maintainers / implementation planning
Purpose: define the approved phased path from the current Core Memory codebase to the target architecture, and serve as the reference document to consult before each execution phase begins.

---

## 1. Scope

This roadmap translates the current architectural review into a safe execution plan.

It assumes:
- the current repo remains the active codebase
- work proceeds as staged refactor, not rewrite
- behavior preservation is a first-class requirement
- write-side and read-side are distinct subsystems
- root operational scripts and existing integration contracts remain stable until explicitly superseded

---

## 2. Architectural direction

### Current high-level system shape
Core Memory currently contains:
- a strong retrieval/runtime facade (`memory.execute`, `memory.search`, `memory.reason`)
- increasingly clean integration contracts
- a historically shaped but critical write-side memory construction pipeline
- mixed schema layers that still need normalization

### Target high-level system shape
Core Memory should converge on:
1. **Write-side memory construction pipeline**
2. **Read-side retrieval/reasoning pipeline**
3. **Explicit memory surfaces**
4. **Explicit schema layers**
5. **Stable integration contracts**

---

## 3. Non-negotiable principles

### Principle A — Preserve behavior first
No phase should break:
- existing root script invocation paths
- existing automation paths
- current artifact paths unless explicitly migrated
- existing integration endpoints/contracts without intentional transition

### Principle B — Refactor, not rewrite
All early phases are internalization and clarification phases, not system replacement phases.

### Principle C — Add before remove
- establish canonical modules before collapsing wrappers
- establish canonical docs before stubbing old docs
- preserve path compatibility until migration is reviewed

### Principle D — Schema before pipeline movement
Do not deeply refactor write-side internals before the noun system is normalized.

### Principle E — Event-native end state
The target write-side architecture must become **event-native**, not merely event-observable.

Current state:
- events are emitted
- but often as byproducts of surrounding operational flows

Target state:
- events become the authoritative trigger/orchestration boundary for write-side processing

### Principle F — Compatibility wrappers are allowed
Existing root scripts may remain as compatibility entrypoints if they delegate into canonical internals.

---

## 4. Canonical subsystem model

### A. Write-side subsystem
Responsibilities:
- finalized-turn / write trigger handling
- transcript/session source discovery
- bead marker parsing
- bead normalization and validation
- bead persistence
- association maintenance
- promotion/candidate evaluation
- rolling/sliding window generation
- session flush / archive transition
- artifact generation

### B. Read-side subsystem
Responsibilities:
- typed search
- causal reasoning
- archive graph retrieval
- unified runtime memory facade
- confidence / next-action semantics

### C. Memory surfaces
Canonical surfaces:
1. **Session beads**
2. **Rolling window**
3. **Archive graph**
4. **`MEMORY.md` OpenClaw-parallel semantic surface**

### D. Schema layers
Canonical layers:
1. **Bead types**
2. **Edge relationship types**
3. **Operational states/statuses**

---

## 5. Phase map

## Phase T1 — Schema normalization
### Goal
Normalize the vocabulary used across beads, edges, and operational state.

### Includes
- formal canonical bead-type list
- formal edge-type list
- formal state/status list
- eliminate state-encoded bead types as canonical forms
- compatibility normalization for legacy values

### Why first
This is the semantic foundation for every later phase.

### Must not do
- no script relocation
- no pipeline refactor yet
- no artifact path changes

### Exit criteria
- canonical schema split is documented
- legacy inputs are normalized safely
- tests protect compatibility

---

## Phase T2 — Write-side pipeline map + invariant freeze
### Goal
Freeze the current write-side system as it exists before internalization.

### Includes
- end-to-end write-side stage mapping
- trigger mapping
- artifact inventory
- path contract inventory
- CLI contract inventory
- idempotency semantics inventory
- explicit identification of where events are byproduct-only today

### Why second
We need to understand exactly what is being preserved before moving logic.

### Must not do
- no logic relocation yet
- no trigger rewrites yet

### Exit criteria
- current write-side pipeline is fully mapped
- invariants are documented and reviewed
- event-trigger gap is explicitly captured

---

## Phase T3 — Write-side trigger model correction
### Goal
Move the write-side toward an event-native orchestration model.

### Includes
- define authoritative write-side triggers
- identify which current flows should become event-triggered
- ensure event emission is no longer merely a byproduct where canonical orchestration is desired
- keep compatibility scripts operational while they converge on event-aware internals

### Why third
If we internalize pipeline logic before clarifying trigger authority, we risk preserving architectural ambiguity.

### Must not do
- no large storage redesign
- no path churn for scripts in the same phase

### Exit criteria
- target trigger model is explicit
- event-native orchestration path is defined
- compatibility entrypoints are mapped to that model

---

## Phase T4 — Write-side internalization
### Goal
Move root-script business logic into canonical internal write-side modules.

### Includes
- internal write-side module graph
- thin wrapper preservation for `extract-beads.py` and `consolidate.py`
- internal orchestration functions behind stable script entrypoints
- parity tests around current behavior

### Why fourth
Now that schema and trigger authority are understood, internalization becomes safe.

### Must not do
- do not rename root scripts
- do not move root scripts
- do not change artifact paths unless explicitly reviewed later

### Exit criteria
- root scripts are thin wrappers
- canonical write-side internals own the logic
- parity tests demonstrate no behavioral regression

---

## Phase T5 — Memory surface explicitness
### Goal
Make the distinct memory surfaces explicit in docs and implementation boundaries.

### Includes
- document/write-side treatment of session beads vs rolling window vs archive graph vs MEMORY.md
- stabilize truth hierarchy semantics
- reduce ambiguity between transcript truth and durable memory truth

### Exit criteria
- contributors can clearly identify which surface serves which purpose
- docs and runtime semantics align

---

## Phase T6 — Read-side/runtime hardening
### Goal
Continue polishing the already-strong retrieval/reasoning side.

### Includes
- consistency hardening
- contract clarity
- confidence calibration improvements where needed
- contributor-facing clarity improvements

### Exit criteria
- runtime memory facade remains canonical and well documented
- read-side behavior remains deterministic and well validated

---

## Phase T7 — Optional orchestration consolidation
### Goal
Only after earlier phases succeed, consider stronger orchestration centers (for example a central runtime engine module) if still justified.

### Includes
- evaluate whether `memory_engine.py` or equivalent is still needed
- only introduce if boundaries are now stable enough to support it cleanly

### Exit criteria
- orchestration simplification is earned, not imposed prematurely

---

## 6. Immediate phase priorities

### Near-term execution order
1. **T1 — Schema normalization**
2. **T2 — Write-side pipeline map + invariant freeze**
3. **T3 — Write-side trigger model correction**
4. **T4 — Write-side internalization**

### Read-side and broader architecture work after that
5. T5
6. T6
7. T7 (optional)

---

## 7. Specific cautions

### Do not do these early
- do not relocate root scripts into `/scripts`
- do not rename root script entrypoints
- do not merge write-side and read-side concerns
- do not let target architecture language outrun preserved behavior
- do not centralize too early around a single orchestrator file without stable subsystem boundaries

### Watch for these risks
- accidental CLI contract drift
- artifact path changes by cleanup enthusiasm
- state encoded in bead type continuing to leak into canonical paths
- event-driven language masking non-event-native implementation reality

---

## 8. How to use this roadmap

Before each implementation phase starts:
1. re-read this roadmap
2. confirm which phase is active
3. confirm phase scope and no-go boundaries
4. confirm preserved contracts/invariants for that phase
5. reject any opportunistic work that belongs to later phases

This document is the canonical planning reference for the transition effort until superseded by a later reviewed roadmap.
