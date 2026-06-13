# Confidence Classes (C / B / A)

Confidence class is the **truth/governance status** of a bead. It answers
"how trusted is this record?" — distinct from myelination, which answers
"how often is this path used?".

| | Confidence class | Myelination |
|---|---|---|
| Measures | truth / governance status | edge / use strength |
| Lives on | the bead (`confidence_class`) | associations (edge weights, bonuses) |
| Changes via | lifecycle events (recall, promotion, user confirmation) | traversal and reinforcement |
| Answers | "why didn't this incorrect thing become permanent?" | "why did retrieval prefer this path?" |

| Class | Meaning | How a bead gets here |
|---|---|---|
| `C` | captured candidate | uncorroborated inference, not yet reinforced |
| `B` | reinforced / used / supported | recalled, marked a candidate, **or source-supported from birth** (see grounding) |
| `A` | canonical / user-confirmed / operationally trusted | promoted, or confirmed by the user |

## Grounding gates the ladder

C/B/A is *one* framework, but the **epistemic grounding** of a bead — *how do
we know it?* — gates where it can sit on the ladder. Grounding is a retained
field (`grounding`) and an input to the class computation; it is distinct from
both the lifecycle (how vetted over time) and `authority` (who asserted it).

| `grounding` | Meaning | Effect on C/B/A |
|---|---|---|
| `observed` | primary source / system of record / direct user statement | enters at **B** (source-supported); can reach A |
| `extracted` | parsed from a document or structured field | enters at **B**; can reach A |
| `inferred` | agent reasoning over beads | enters at C; A only via promotion/confirmation |
| `speculative` | hypothesis / overlay, untested | enters at C; **capped at B** until validated |

Why this matters: it makes the "why didn't this incorrect thing become
permanent?" guarantee *structural*. A speculative bead **cannot** reach A
while it is speculative — not via recall, not even via promotion. The only way
out of the cap is for the grounding itself to upgrade: a hypothesis whose
`hypothesis_status` flips to `validated` is no longer speculative, and user
confirmation lifts a speculative bead to `inferred` (the human has grounded
it). Conversely, a primary-source observation is trusted (B) from birth rather
than starting at C, because it is supported by its source before any use.

Grounding defaults from bead type (`GROUNDING_BY_TYPE`): the external/source
types (`structured_observation`, `operational_event`, `document_reference`,
`transcript`) and `evidence` default to `observed`; agent reasoning types
(`decision`, `lesson`, `state_assertion`, `data_insight`, …) to `inferred`;
`hypothesis` to `speculative`. Connectors may set `grounding` explicitly (e.g.
a claim parsed out of a document → `extracted`).

## Rules

- **Monotonic.** The class never lowers. An incorrect bead doesn't get
  demoted — it gets **superseded** (status change + supersession chain),
  which removes it from current-truth retrieval entirely. Trust and truth
  are separate axes: supersession answers "is this still true?", the class
  answers "how vetted was it?".
- **Floor derivation.** `Bead.from_dict` raises the stored class to the
  floor implied by lifecycle fields (`promoted`, `authority=user_confirmed`,
  `recall_count`, `promotion_candidate`), so legacy beads read with a
  correct class without migration.
- **Why C beads don't become permanent.** Promotion gates and the rolling
  window only archive beads with reinforcement signals; a captured-but-never-
  used C bead compacts away. This is the explicit answer to "why didn't this
  incorrect thing become permanent?" — it never earned B, and nothing but
  promotion or the user can grant A.

## Vocabulary

`schema/normalization.py`:
- `CONFIDENCE_CLASSES = {"C", "B", "A"}` with aliases (`captured`→C,
  `reinforced`/`used`/`supported`→B, `canonical`/`confirmed`/`trusted`→A),
  `normalize_confidence_class`, `confidence_class_rank`,
  `derive_confidence_class`.
- `GROUNDING_LEVELS = {"observed", "extracted", "inferred", "speculative"}`
  with aliases and `GROUNDING_BY_TYPE`, `normalize_grounding`,
  `derive_grounding`, `resolve_grounding`.
- `resolve_confidence_class(bead)` is the single entry point used by every
  write path: `max(provided floor, derived)` then apply the speculative
  ceiling. `derive_confidence_class` is grounding-aware.

## Confirmation surface

User confirmation is the only path to A besides promotion:

- Public API: `core_memory.confirm_bead(root, bead_id, note="")`
- Store: `MemoryStore.confirm(bead_id, note="")`
- HTTP: `POST /v1/memory/confirm` with `{"bead_id": "...", "note": "..."}`

Confirmation sets `authority=user_confirmed` and `confidence_class=A`,
appends a `bead_confirmed` event, and updates both the index projection and
the session surface. Content is never edited — confirmation is a governance
act on an immutable record.

Claims do not need a parallel surface: the claim layer already records
governance decisions through `ClaimUpdate` (`reaffirm` / `supersede` /
`retract` / `conflict`); a user confirmation of a claim is a reaffirm
decision carried by a `user_confirmed` bead.
