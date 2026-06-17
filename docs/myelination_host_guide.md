# Myelination — Host Integration Guide

**Audience:** the engineer (or coding agent) integrating a connector host with
Core Memory's myelination layer.
**Scope:** how the host *drives* and *consumes* edge reinforcement. Core Memory
owns the reward math and the manifest; the host supplies audited decisions and
reads the manifest.

Myelination reinforces **association edges**, never beads/claims/truth. It
answers one question: *which traversal paths have demonstrably helped?* It is a
projection over audited events — turning it off changes ranking, never memory.

---

## 1. Mental model

```
audited events  ──►  reward/decay (edge-level)  ──►  manifest  ──►  retrieval ranking
  • retrieval feedback        bonus_by_edge_key        bonus_by_edge_key
  • human approval/reject     bonus_by_bead_id         bonus_by_bead_id (projection)
  • goal resolution
  • dreamer candidate decisions
  • claim-conflict resolution
```

Two layers fuse into one manifest:
- **Telemetry** — retrieval-feedback success/failure on traversed edges
  (`min-hits`-gated, so noisy telemetry needs corroboration).
- **Reward events** — audited decisions, written to
  `.beads/events/myelination-rewards.jsonl`, that bypass the telemetry min-hits
  filter (they're explicit, not noisy).

`bonus_by_edge_key` is the source of truth; `bonus_by_bead_id` is a projection
onto endpoint beads for scorer compatibility — **never read it as bead decay.**

---

## 2. Enable it

| Env var | Default | Meaning |
|---|---|---|
| `CORE_MEMORY_MYELINATION_ENABLED` | `0` | master switch (off by default) |
| `CORE_MEMORY_MYELINATION_REWARD_EVENTS_ENABLED` | = master switch | fuse reward events into the manifest |
| `CORE_MEMORY_MYELINATION_SINCE` | `30d` | window for feedback + reward events |
| `CORE_MEMORY_MYELINATION_LIMIT` | `1000` | max feedback rows scanned |
| `CORE_MEMORY_MYELINATION_REWARD_EVENT_LIMIT` | `2000` | max reward events scanned |
| `CORE_MEMORY_MYELINATION_MIN_HITS` | `2` | telemetry corroboration threshold (does **not** gate reward events) |
| `CORE_MEMORY_MYELINATION_POS_CAP` | `0.12` | per-edge positive bonus cap |
| `CORE_MEMORY_MYELINATION_NEG_CAP` | `0.08` | per-edge negative bonus cap |
| `CORE_MEMORY_MYELINATION_REWARD_STRENGTH` | `0.04` | default per-reward-event nudge |

When the master switch is off, the manifest is well-formed but empty
(`enabled: false`), so consumers can read it unconditionally.

---

## 3. The host doesn't emit reward events directly

Reward events are produced **automatically** by Core Memory when the host calls
the audited governance surfaces. The host's job is to call those surfaces; the
reinforcement is a side effect. This keeps the edge-only and audit invariants in
Core Memory, not in every host.

| Audited action (host calls) | Reward emitted | Edge reinforced/weakened |
|---|---|---|
| `POST /v1/memory/confirm` / `approve` | positive | the bead's concrete supporting edges |
| `POST /v1/memory/reject` | negative | the bead's concrete supporting edges |
| goal resolution (turn pipeline) | positive | the audited `outcome --resolves--> goal` edge |
| `decide_dreamer_candidate` accept / reject | positive / negative | the candidate's `source\|rel\|target` edge |
| claim-conflict resolution (`prefer_a`/`prefer_b`/`retract_both`) | mixed | preferred claim's path reinforced, contradicted weakened |

Rules Core Memory enforces so the host can't get them wrong:
- **Concrete edges only.** A decision with no resolvable supporting edge emits
  no reward (it's recorded for governance, but nothing is myelinated). Reward is
  never smeared across a bead and its unrelated neighbours.
- **Existence-checked.** An edge is rewarded only if it actually exists in the
  graph (or, for evidence/recall paths, was a real supporting edge).
- **Normalized relations.** Edge keys use canonical relations
  (`caused_by` -> `causes`), so reward and telemetry fuse and consumers find them.
- **Idempotent for audited decisions.** Reward events carry a deterministic
  fingerprint over `(source_type, source_event_id, polarity, sorted edge_keys)`.
  Retrying the same audited decision returns the original reward event instead
  of appending another one. Distinct decisions should carry distinct stable
  `source_event_id` values.
- **`both_valid` is scoped** — context forks are never punished.

---

## 4. Refresh the manifest

Reward events accumulate in the log; they fuse into the manifest when it is
(re)computed. Trigger a recompute via the async job:

```
POST /v1/ops/async-jobs/enqueue   { "kind": "myelination-update" }
POST /v1/ops/async-jobs/run
```

The job writes `.beads/events/myelination-manifest.json`:

```json
{
  "schema": "core_memory.myelination_manifest.v2",
  "enabled": true,
  "bonus_by_edge_key": { "out1|resolves|goal3": 0.04, "evA|supports|decB": 0.09 },
  "bonus_by_bead_id":  { "decB": 0.045 },
  "stats": { "events": 12, "edges": 7, "beads": 9, "strengthened": 6, "weakened": 1 },
  "source_event_counts": { "human_approval": 4, "goal_resolution": 2, "claim_conflict_resolution": 2 },
  "config": { "since": "30d", "min_hits": 2, "pos_cap": 0.12, "neg_cap": 0.08, "reward_events": true }
}
```

Run it on a cadence (e.g. nightly, or after a batch of decisions). The host does
not parse the rewards log — only the manifest.

---

## 5. What the host reads

For analysis/UI, read the manifest (or `myelination_report` for ranked
top-strengthened / top-weakened edges and beads). Retrieval consumes the
manifest internally — the host does not need to inject bonuses into queries.

`source_event_counts` tells the host *why* edges moved (how much came from human
approvals vs. goal resolutions vs. claim conflicts) — useful for surfacing "the
system learned this path from your approvals" in a UI.

---

## 6. Invariants (so the host doesn't reimplement them)

- Myelination changes **traversal preference, never truth.** It never mutates
  beads, claims, or C/B/A.
- Edge-level only. The one legacy exception is `apply_contradiction_decay`, a
  bead-level down-weight applied to beads carrying *active, unresolved* claim
  conflicts at manifest-compute time — distinct from the edge-level reward fired
  when a conflict is *resolved*. It is retained for compatibility; do not model
  new decay on it.
- No time decay. Older paths are not inherently worse; decay is event-driven.
- Reward events are audited and append-only — a durable record of which decisions
  shaped traversal, independent of the manifest.

---

## 7. Integration checklist

1. Set `CORE_MEMORY_MYELINATION_ENABLED=1` (and confirm
   `REWARD_EVENTS_ENABLED` follows or set it explicitly).
2. Drive governance through the audited surfaces (confirm/approve/reject, goal
   resolution, candidate decisions, conflict resolution) — reward emission is
   automatic.
3. Schedule the `myelination-update` job to refresh the manifest.
4. Read the manifest (and `source_event_counts`) for analysis/UI; let retrieval
   consume it internally.
5. Tune caps/`min_hits`/`reward_strength` per env if needed; defaults are safe.
