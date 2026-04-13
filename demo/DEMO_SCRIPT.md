# Demo Script (Architecture-Aligned)

Use this script when presenting Core Memory behavior.

## 1) Start from canonical turn writing

- Send a user turn in **Chat**.
- Point out: turn ingestion routes through canonical finalized-turn path.
- Show new bead(s) in **Memory** and turn-linked provenance.

## 2) Show claim-first state

- Open **Claims** tab.
- Highlight slot status (`active`, `conflict`, etc.), current value, and conflict counts.
- Click a slot to show **history** and **timeline/update** entries.
- Use **as_of** control to demonstrate temporal current-state replay.
- Note: selected slot + as_of are reflected in URL query params for shareable review links.
- Explain that claim-state is a first-class read model, not inferred only from free text.

## 3) Explain answer grounding

- After a chat answer, show **Runtime → Last Answer Diagnostics**:
  - `answer_outcome`
  - `source_surface`
  - `anchor_reason`
  - `retrieval_mode`
- Emphasize partial/abstain is preferred over hallucination when grounding is weak.

## 3.5) Show entity registry awareness

- Open **Entities** tab.
- Highlight active vs merged entity counts.
- Show alias normalization/provenance fields on a row.
- Open a merge-proposal row (if present) to demonstrate entity merge observability.
- Use **Suggest merges** then adjudicate one proposal (accept/reject) to show reviewer-driven merge flow.

## 4) Show runtime health

- In **Runtime**, cover:
  - queue state
  - per-queue side-effect breakdown (semantic_rebuild / compaction / side_effects)
  - semantic backend status
  - strict/degraded mode + multi-worker safety badges
  - connectivity diagnostics when external backends are configured
  - last flush summary
  - recent flush history (trigger, session rotation, rolling-window bead count)
  - last-answer diagnostics (warnings, chain count, top bead IDs)
  - myelination snapshot
- Explain degraded vs semantic-required implications.

## 5) Run benchmark safely

- Open **Benchmark** controls.
- Set `root=snapshot` (or `clean`) and run.
- Explain benchmark is isolated and does not mutate live demo memory.
- In Benchmark tab, review:
  - summary cards
  - per-bucket accuracy
  - failing-case drilldown
  - backend mode + token/latency context

## 6) Archive/hydration awareness

- Open a bead in Memory.
- Show hydrated turn sources in modal payload when available.
- Explain archived detail is recoverable via hydration surfaces.

## What not to claim

- Do not claim benchmark writes into live store.
- Do not claim lifecycle is only candidate/promoted.
- Do not claim every answer is current-state truth.
- Do not describe direct file mutation as normal operation.
