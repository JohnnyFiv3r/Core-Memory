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
| `C` | captured candidate | every new bead starts here |
| `B` | reinforced / used / supported | recalled at least once, or marked a promotion candidate |
| `A` | canonical / user-confirmed / operationally trusted | promoted, or confirmed by the user |

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

`schema/normalization.py`: `CONFIDENCE_CLASSES = {"C", "B", "A"}` with
aliases (`captured`→C, `reinforced`/`used`/`supported`→B,
`canonical`/`confirmed`/`trusted`→A), `normalize_confidence_class`,
`confidence_class_rank`, `derive_confidence_class`.

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
