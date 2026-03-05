# Causal Grounding: Problem Analysis + Solution Plan

## Context
We implemented and applied immutable structural sync to enforce:

- associations -> links
- links -> immutable structural edge events
- graph materialization from edge events

The sync pipeline itself is healthy, but causal grounding KPI remains low.

---

## Latest Applied Test Results

## 1) Structural Sync Apply (strict)
Command:

```bash
core-memory --root /home/node/.openclaw/workspace/memory graph sync-structural --apply --strict
```

Result:

```json
{
  "ok": true,
  "apply": true,
  "strict": true,
  "associations_scanned": 0,
  "links_added": 0,
  "missing_edge_from_link": 9,
  "edges_applied": 9,
  "invariants": {
    "missing_link_from_association": 0,
    "missing_graph_head_from_edge": 0
  }
}
```

Interpretation:
- Pipeline executed successfully.
- Missing edge events from existing links were backfilled.
- Strict invariant checks passed.

## 2) Retrieval Eval After Apply
Command:

```bash
python eval/retrieval_eval.py
```

Result (summary):

```json
{
  "cases": 2,
  "recall_at_5": 1.0,
  "mrr": 1.0,
  "median_latency_s": 0.0904,
  "p95_latency_s": 0.104,
  "deterministic": true,
  "low_info_citation_rate": 0.1667,
  "causal_grounding_rate": 0.0
}
```

Interpretation:
- Retrieval quality and determinism remain strong.
- Causal grounding did **not** improve.

## 3) Runtime Probe for Causal Query
Query:

`why did we move to candidate-first promotion`

Observed runtime outcome:
- selected route: `remember`
- chains: 1
- chain edge count: 0
- citations include decision/outcome types, but no structural edge evidence

Interpretation:
- The final selector can still choose non-structural output for causal intent.
- This is now a **selection/output-shape issue**, not structural sync integrity.

---

## Problem Statement

The system now has structural truth available, but causal questions can still end with non-structural final chains. As a result, the causal grounding predicate fails even when relevant beads are retrieved.

Current failure mode:
1. Causal query enters pipeline
2. `why` path may be weak/noisy
3. fallback selection chooses `remember` due to quality scoring
4. final chains lack structural edges
5. grounding KPI stays false

---

## Desired Behavior

For causal intents (`why`, `what happened`, `decide`, `because`, `rationale`):

- Prefer structurally grounded chains in final answer selection.
- Only return non-structural context when no structural candidates exist in radius-1 neighborhood.
- Make this explicit in output (grounded vs ungrounded reason).

---

## Leading Solution A (Recommended)

## Causal Route Lock + Structural Finalizer

### Plan
1. Treat `why` as authoritative for causal intents.
2. If `why` output has structural chains, keep it.
3. If not, inject deterministic radius-1 structural fallback chains (graph -> associations -> links).
4. Re-rank/reselect with structural-priority scoring.
5. Only allow non-structural `remember` fallback if:
   - no structural candidates are available at all.

### Why this is best
- Minimal complexity increase.
- Deterministic and inspectable.
- Directly targets causal grounding KPI.

### Acceptance criteria
- For causal queries, if any structural candidate exists, final output includes at least one structural edge chain.
- `causal_grounding_rate` increases on KPI fixture.
- Determinism unchanged.

---

## Leading Solution B

## Dual Candidate Selector with Hard Causal Constraint

### Plan
1. Compute both `why` and `remember` outputs.
2. For causal intent, select best candidate satisfying structural grounding predicate.
3. If none satisfy predicate, fall back to best overall and mark ungrounded.

### Trade-offs
- More robust than route lock in edge cases.
- Slightly higher complexity in selector layer.

---

## Optional Supporting Solution C (Data-first)

## Targeted Structural Curation for KPI Cases

Add/repair missing structural links for gold fixture incidents where expected causal links are known.

Use only as complement to A/B, not as substitute.

---

## Recommended Execution Order

1. Implement Solution A.
2. Re-run eval + per-case diagnostics.
3. If grounding still low due to sparse links, apply Solution C for fixture coverage.
4. If needed, implement Solution B for selector robustness.

---

## Risk Notes

- Over-constraining causal selection can reduce conversational flexibility.
- Mitigation: allow non-structural fallback only with explicit ungrounded marker.

- Benchmark gaming risk with tiny KPI set.
- Mitigation: expand fixture to 10-15 human-reviewed cases after selector fix.

---

## Bottom Line

The immutable causal pipeline is now functioning correctly.
The remaining issue is final answer selection for causal queries. Fixing causal selection policy (A) is the highest-value next move.
