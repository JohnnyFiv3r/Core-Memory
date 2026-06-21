# PRD 1: SOUL Files
## Agent-Maintained Self-Model and Goal Hierarchy

**Status:** Draft v3
**Supersedes:** `docs/reports/soul-synthesis-spec.md` (the 2.0 "SOUL.md Synthesis Specification")

> **Review fixes folded into this draft (vs. the external draft):**
> - Added §0 Supersession (the auto-synthesis → agent-authored shift).
> - Added §4.1 Source of truth (markdown is a projection; structured revision records are authoritative).
> - Added §4.2 Scope & file location (per-`(root, subject)`, `.beads/identity/<subject>/`).
> - Added §4.3 Working-memory injection (the consumption path — the one mechanic carried over from the old spec).
> - Added §6.0 Goal Bead definition + goal-lifecycle states (shared dependency, not a new bead type).
> - Clarified §5.5 IDENTITY.md *projects from* the claims layer (no parallel identity store).
> - Endpoints standardized under `/v1/soul/*` with existing auth; concurrency model added (§13.0).
> - Brand-neutral throughout (no deployment-specific names — public naming guard).

---

## 0. Supersession

This PRD replaces the prior SOUL design in `docs/reports/soul-synthesis-spec.md`,
which modeled SOUL.md as **auto-synthesized derived state** produced by a
`soul-synthesis` job and explicitly *"not authored directly by the agent or the
user."* This PRD inverts that: SOUL is an **agent-authored** self-model,
maintained under guardrails, with Dreamer as evidence source and humans as
authority.

One mechanic survives the change unchanged: **SOUL is injected into working
memory at session start, not retrieved on demand** (see §4.3). The agent does
not look up its identity — it has one. What changes is *who writes it*: the
agent, deliberately, citing evidence — not a synthesis job compressing the
graph.

The old spec also assumed *time-decayed* myelination. Per the Myelination V2
PRD, time decay is removed; SOUL relies only on event-driven myelination signals.

---

## 1. Purpose

SOUL Files provide a persistent, agent-maintained self-model for a user,
organization, project, or agent operating within Core Memory.

SOUL Files are not memory stores. They are a maintained **theory of self**: a
compact, revisable representation of identity, goals, tensions, worldlines,
observed behavior, and endorsed direction.

The self is not stored in the files. The self emerges from the interaction
between memory, behavior, goals, tensions, human feedback, and historical
continuity. SOUL Files are the current theory of that self.

---

## 2. Core Principles

### 2.1 The Self Is Not The Model
SOUL Files are not the self. They are the system's current best explanation of
itself. The actual self exists across beads, claims, worldlines, goals,
tensions, human interactions, repeated behavior, and historical continuity.
SOUL Files are a lossy compression of those systems.

### 2.2 Facts Are Not Identity
Facts describe the world the self-model must persist within. Facts are not
identity.
- Facts: a company has 50 employees; a project launched in 2026; a user lives
  in Tennessee.
- Identity-level statements: the system pursues continuity; the company
  optimizes for operational clarity; the user consistently builds bridges
  between unrelated systems.

Facts support identity. Facts do not constitute identity.

### 2.3 Identity Emerges From Goals And Tensions
Goal hierarchy and selfhood are inseparable. Identity emerges from persistent
goals, persistent tensions, repeated decisions, repeated behaviors, human
approval, and historical continuity. Identity is **derived, not manually
declared once.**

### 2.4 Humans Anchor Reality
Human participants are part of the reward and governance loop. Humans anchor the
system by approving goals, rejecting goals, correcting the agent, confirming
identity statements, rejecting inaccurate self-model claims, and interacting in
ways that create new evidence. Human approval is the strongest trigger for goal
pursuit; human rejection is the strongest negative signal.

### 2.5 Dreamer Is A Scientist
Dreamer does not directly author SOUL Files. Dreamer observes, measures, infers,
and surfaces findings. Dreamer may read SOUL Files as part of its research, but
its outputs are scientific findings, not direct self-model edits. Dreamer
produces findings, hypotheses, goal candidates, identity candidates, tension
candidates, assembly-depth measurements, observed-vs-endorsed divergence
reports, and proposed revisions. The agent authors SOUL updates using Dreamer
outputs under guardrails.

### 2.6 SOUL Is Falsifiable
SOUL Files are not ground truth. They are revisable theories. Even endorsed
identity can be wrong. The system preserves the distinction between what is
observed, what is inferred, and what is endorsed.

---

## 3. Relationship To Core Memory C/B/A Hierarchy

SOUL epistemics must not replace or interfere with Core Memory's C/B/A
hierarchy. C/B/A remains the canonical truth/governance status of beads. SOUL
epistemics are a projection layer over Core Memory evidence.

### 3.1 C/B/A Governs Beads
C/B/A answers: how trusted is this memory record? C = captured candidate;
B = reinforced / supported / source-backed; A = canonical / user-confirmed /
operationally trusted.

### 3.2 SOUL Epistemics Govern Self-Model Statements
SOUL statements use a separate vocabulary — **Observed / Inferred / Endorsed** —
that describes the status of self-model statements, not bead trust.

### 3.3 Mapping
- **Observed** SOUL statement: derived primarily from observed/extracted B/A
  evidence (grounding `observed`/`extracted`).
- **Inferred** SOUL statement: derived from inferred/speculative evidence,
  Dreamer findings, or behavioral patterns (grounding `inferred`/`speculative`).
- **Endorsed** SOUL statement: human-approved, backed by A-class confirmation or
  explicit approval (`authority=user_confirmed`). Endorsement lives on the
  C/B/A + authority axis, not on grounding.

### 3.4 Guardrail
SOUL endorsement does not mutate a bead's C/B/A class unless it goes through the
existing Core Memory confirmation pathway (`confirm_bead`/`approve_bead`). SOUL
is a projection; Core Memory remains the evidentiary substrate.

---

## 4. SOUL File Set

SOUL is a collection of files rather than one monolithic document:
`SOUL.md`, `GOALS.md`, `TENSIONS.md`, `WORLDLINES.md`, `IDENTITY.md`.

There is no separate `VALUES.md`. Values are emergent and live inside
`IDENTITY.md`.

### 4.1 Source of truth

The markdown files are the **human-readable projection**. The **authoritative
source of truth is an append-only set of structured revision records** (mirroring
the storyline-overlay / observation pattern). Every SOUL update is a structured
record (`soul_revision.v1`, §14) carrying source, epistemic status, evidence,
and diff. The markdown is rendered from the current accepted revision set.

Consequence:
- Read endpoints serve rendered markdown.
- Proposal/approval/diff and integrity operations run on the structured records.
- Continuity (revision history, supersession) lives in the record stream, not in
  markdown git diffs.

### 4.2 Scope & file location

SOUL is scoped per `(root, subject)` where `subject ∈ {user, org, project,
agent}`. Files and their backing records live at
`.beads/identity/<subject>/` (preserving the prior `.beads/identity/`
convention). Default v1: a single identity scope per root — the primary
self (the agent/tenant). Multiple subjects per root is a documented extension,
not a v1 requirement. SOUL is tenant-isolated like the rest of Core Memory
(per-root, `X-Tenant-Id`).

### 4.3 Working-memory injection (consumption)

SOUL Files are injected into working memory at **session start** via the turn
pipeline (`turn_flow.py` / `process_session_start`), not retrieved on demand.
The agent reasons *from* its self-model every session rather than rediscovering
it. `SOUL.md` (the synthesis) is the primary injected surface; `GOALS.md`,
`TENSIONS.md`, and `IDENTITY.md` may be injected when they have content. The
prompt renderer groups applied entries under visible `Endorsed`, `Observed`, and
`Inferred` sections so agents do not flatten inferred self-model content into
settled identity. This is the one mechanic carried forward from the superseded
synthesis spec.

---

## 5. File Responsibilities

### 5.1 SOUL.md
High-level synthesis of the current self-model: executive identity summary,
dominant goals, dominant tensions, active worldlines, current observed/endorsed
divergence, revision-history summary. Answers: Who are we? What are we trying to
become? What tensions define us? What must persist for continuity to survive?

### 5.2 GOALS.md
Persistent goal hierarchy: endorsed goals, active goal candidates, completed
goals, abandoned goals, decaying goals, goal lineage, parent/child relationships,
human-approval state, related goal beads, related worldlines, related tensions.
Goals originate from explicit human declaration, human approval, goal beads,
Dreamer-proposed candidates, or organizational authority. Goals are pursued
automatically only when human-approved or when guardrail criteria are satisfied;
human approval always authorizes pursuit. (Goal-lifecycle states: §6.0.)

### 5.3 TENSIONS.md
Persistent unresolved tensions: active, resolved, candidates; assembly depth;
recurrence evidence; related goals/worldlines/identity claims. Examples:
customization vs standardization; speed vs accuracy; autonomy vs control;
growth vs stability. Tensions are first-class because they may be the most
stable substrate of continuity.

### 5.4 WORLDLINES.md
Long-lived trajectories: active worldlines, branches, inflection points, related
beads/goals/tensions, continuity notes. A worldline represents continuity
through time. Worldlines are derived (graph backbones); this file is a
human-facing projection of the worldline/storyline surfaces, not a second store.

### 5.5 IDENTITY.md
Explicit self-model: Observed Self, Endorsed Self, Emergent Values, Aspirational
Values, Identity Tensions, identity-divergence notes.
- Observed Self: what evidence indicates the system currently is.
- Endorsed Self: what humans and/or the agent have approved as desired selfhood.
- Emergent Values: stable preferences revealed through repeated goals, behavior,
  and human endorsement.
- Aspirational Values: values stated/endorsed but not yet strongly observed.

**IDENTITY.md projects from the claims layer.** The `identity` and `preference`
claim kinds (resolved via `resolve_all_current_state`) are the substrate;
IDENTITY.md cites the claims/beads it derives from and does **not** store
identity facts as an independent source of truth. This prevents a second
identity store drifting from the claims layer. Values emerge from durable
patterns of pursuit; they are not authored as standalone truth.

---

## 6. Goal Beads

### 6.0 Definition and lifecycle (shared dependency)

A **Goal Bead is not a new bead type** — it is a `BeadType.GOAL` bead carrying
authoritative goal-event semantics (`goal_id`, `success_criteria`, and the goal
lifecycle state below). Today the goal lifecycle supports `candidate` and
`resolved` (`goal_status`, `promotion_state`, `promotion_decision="resolve_goal"`,
`resolved_by_bead_id`, and a `resolves` association — all already implemented in
`promotion_service.py`).

This PRD (and the Dreamer/Myelination PRDs) depend on extending that lifecycle
with these states: **endorsed, active, completed, abandoned, decaying.** That
extension is a **named shared dependency** ("Goal Lifecycle v2") used by GOALS.md,
Dreamer goal discovery, and Myelination goal-resolution reward. Goal lineage and
parent/child relationships are future scope, not v1.

**Upstream primitive (forward look):** a goal may sit atop a deeper **target
state** — an inferred attractor describing *what state the system navigates
toward*, distinct from the linguistic goal that names it (Dreamer PRD §31,
"Dreamer V4"). The intended hierarchy is `observed behavior → implied target
state → goal candidate → human-endorsed goal → SOUL goal hierarchy`. SOUL is the
layer that **endorses a target state into a goal**; until endorsed, a target
state is a Dreamer inference, never a goal. GOALS.md should be designed so that a
goal can later record the target state it endorses, without requiring it in v1.

### 6.1 Valid Goal Bead Sources
Explicit human declaration; human approval/rejection; explicit human correction;
explicit goal completion; explicit goal abandonment; authorized
system-of-record goal event. Examples: "We need to reduce onboarding friction";
user clicks Approve/Reject Goal; "No, that is not our goal"; "We are abandoning
the SMB-first strategy"; "This goal is complete."

### 6.2 Invalid Goal Bead Sources
Dreamer inference, behavioral pattern detection, assembly-depth analysis,
identity hypothesis, repeated behavior alone, agent guesswork. If Dreamer
observes that decisions repeatedly favor explainability, it may create a
**goal candidate**, never a Goal Bead. Goal Beads are authoritative observations;
Dreamer findings are hypotheses.

---

## 7. Epistemic Tiers

- **Observed** — direct evidence (human stated a goal; a source document states a
  policy; a user approved/rejected).
- **Inferred** — belief from reasoning over evidence (Dreamer detects recurring
  behavior; identifies a latent goal; finds identity divergence; proposes a value
  candidate).
- **Endorsed** — a human or approved authority confirmed the statement as
  acceptable for the self-model. Endorsed statements can still be revised;
  endorsement does not make a statement permanently true.

---

## 8. SOUL Write Triggers

Valid trigger classes: human interaction, Dreamer run, SOUL integrity check.

### 8.1 Human Interaction
Triggers updates when it creates an authoritative event: user approves/rejects a
proposed goal; states a goal; corrects the agent; confirms/rejects an identity
statement. The underlying artifact is usually a Goal Bead, correction bead,
confirmation event, or rejection event.

### 8.2 Dreamer Run
Dreamer runs periodically (initially nightly), evaluating goal completion /
abandonment / decay, identity divergence, new/resolved tensions, worldline
changes, assembly-depth changes, observed-vs-endorsed drift, and self-model
contradictions. Dreamer emits findings; the agent then decides whether to apply
an auto-eligible update, create a proposed update, request human approval, or
take no action. Dreamer findings do not directly write SOUL — they trigger agent
review.

### 8.3 SOUL Integrity Check
Structural integrity checks (broken references, missing links, duplicates,
conflicting status markers, invalid structure). Not identity evolution —
document maintenance. Integrity fixes may auto-apply if they do not alter
identity, goals, or endorsed meaning.

---

## 9. Governance Modes

### 9.1 Approval Required
`Dreamer finding → agent proposed diff → human approval → SOUL update`. Safest.

### 9.2 Auto
`Dreamer finding → agent evaluates guardrails → agent applies eligible update or
creates proposal`. Even in auto mode, these always require explicit human
approval: new top-level endorsed goal; removal of a human-endorsed goal; major
endorsed identity rewrite; deletion of a long-lived tension; governance-mode
change; change to the human-authority model. Auto mode is not unconstrained
self-modification.

---

## 10. Agent Writing Guardrails

- Preserve continuity over replacement; prefer revision over deletion; never
  erase prior identity — supersede or archive it.
- Separate observed self from endorsed self.
- Treat stated values as claims until behavior or humans support them.
- Treat Dreamer findings as evidence, not authority.
- Prefer small diffs over large rewrites; every substantive update cites source
  evidence.
- Human approval overrides inferred identity; human rejection creates negative
  reinforcement.
- If uncertain, propose rather than apply.
- Do not create Goal Beads from inference.
- Do not promote a goal to pursuit without approval or explicit guardrail
  satisfaction.
- Do not mutate Core Memory C/B/A state through SOUL updates.
- Maintain catastrophic-forgetting resilience as a standing meta-goal (§11).

**Core rule: the agent authors SOUL, but it must not hallucinate selfhood.**

---

## 11. Continuity Preservation Meta-Goal

SOUL maintenance preserves continuity whenever possible. Preserve long-lived
goals, tensions, worldlines, human-endorsed identity, historical lineage, and
superseded-but-meaningful prior states. Prefer revision over replacement and
supersession over deletion.

**Recovery from partial memory loss (mechanism, not just aspiration):** because
the markdown is a projection of structured revision records (§4.1) which are
themselves derived from beads/claims/worldlines, SOUL can be **re-derived** from
the graph if the projection is lost. v1 guarantees the revision records are
durable and replayable; full automated re-derivation is a documented future
capability, not a v1 deliverable.

---

## 12. Goal Decay

Goals may decay; decay is separate from bead deletion (beads are persistent;
edges and goals may weaken). A goal may decay when unreferenced for a long
period, no longer supported by behavior, in conflict with newer endorsed goals,
detected as abandoned by Dreamer, deprioritized by humans, or when related
worldlines end / tensions resolve. Decayed goals remain retrievable, marked
lower-priority, stale, dormant, superseded, or abandoned. (Goal decay is a
Goal-Lifecycle-v2 / SOUL governance decision, not a Myelination action.)

---

## 13. Endpoint Surface

All SOUL endpoints are under `/v1/soul/*`, reuse the existing
`CORE_MEMORY_HTTP_TOKEN` auth and `X-Tenant-Id` scoping, and are separate from
Core Memory capture/recall/confirmation surfaces.

### 13.0 Concurrency
SOUL writes serialize through the existing `store_lock`. The backing store is
append-only revision records; the markdown projection is recomputed under lock.
Human, Dreamer, and integrity triggers all produce **proposals**; the agent
applies them serially — one writer at a time.

### 13.1 Read
`GET /v1/soul/files` — available SOUL files.
`GET /v1/soul/files/{file_name}` — current contents (rendered markdown).
`GET /v1/soul/files/{file_name}/entries` — folded structured entries with
revision provenance for one SOUL file.
`GET /v1/soul/history` — revision history across SOUL Files.

### 13.2 Proposal
`POST /v1/soul/propose-update` — create a proposed SOUL diff.
`POST /v1/soul/apply-update` — apply an approved or auto-eligible update.
`POST /v1/soul/approve-update` — human approval of a proposed update.
`POST /v1/soul/reject-update` — human rejection of a proposed update.

### 13.3 Goals
`POST /v1/soul/goals/propose|approve|reject|complete|abandon|decay`.

### 13.4 Dreamer integration
`POST /v1/soul/dreamer/findings` — receive Dreamer findings.
`POST /v1/soul/dreamer/propose-updates` — generate proposals from findings.
`POST /v1/soul/dreamer/run-review` — run agent review over Dreamer outputs.

### 13.5 Integrity
`POST /v1/soul/integrity/check` — check consistency.
`POST /v1/soul/integrity/repair` — apply structural repair if safe.

---

## 14. Minimum Update Payload (`soul_revision.v1`)

```json
{
  "schema": "soul_revision.v1",
  "target_file": "GOALS.md",
  "update_type": "proposed",
  "source": "human|agent|dreamer|integrity_check",
  "epistemic_status": "observed|inferred|endorsed",
  "reason": "Human approved goal candidate",
  "evidence": [
    {"bead_id": "b_123", "claim_id": "c_456", "confidence_class": "A", "relationship": "supports"}
  ],
  "diff": "...",
  "requires_approval": true
}
```

---

## 15. Success Criteria

Persistent identity across sessions; clear separation of memory and self-model;
clear observed/inferred/endorsed distinction; C/B/A compatibility; goal
continuity across time; human-anchored approval/rejection; Dreamer-driven
self-observation; goal decay without memory deletion; recovery path from partial
memory loss; falsifiable identity evolution; agent-authored self-modeling without
hallucinated selfhood; SOUL injected at session start and demonstrably shaping
behavior.

---

## 16. Non-Goals

SOUL Files do not replace Core Memory or C/B/A; do not store all facts/memories;
do not directly mutate beads; do not auto-trust Dreamer findings; do not let
inference become authority without approval or guardrail satisfaction; do not
treat values as standalone source-of-truth files; do not delete beads as part of
decay.

---

## 17. Summary

Core Memory captures evidence. Dreamer studies evidence. The agent authors SOUL.
Humans anchor authority. C/B/A governs memory confidence;
Observed/Inferred/Endorsed governs self-model statements. Goal Beads represent
authoritative goal events; Dreamer findings represent hypotheses. SOUL Files
represent the current, falsifiable theory of self — agent-authored, evidence-cited,
and injected at session start so the agent reasons from a self rather than
rediscovering one.
