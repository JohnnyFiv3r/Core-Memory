# End-to-End Query Suite Report

Date: 2026-03-05 (UTC)
Scope: 10 representative retrieval/reasoning queries run against current memory state.

## Summary

- Total queries: 10
- Queries with structural chains selected: 9/10
- Queries using retry: 1/10
- Queries selecting `why` route: 9/10
- Queries selecting `remember` route: 1/10

## Per-Query Metrics

### 1) when we had almost all promoted beads what happened
- selected_route: `why`
- intent_class: `causal`
- used_retry: `false`
- causal_intent flag: `true`
- selected_has_structural: `true`
- grounding_reason: `structural_constraint_applied`
- chains: `2`
- edge_counts: `[1, 1]`
- citation_types: `{lesson:1, decision:2}`

### 2) why did we move to candidate-first promotion
- selected_route: `why`
- intent_class: `causal`
- used_retry: `false`
- causal_intent flag: `true`
- selected_has_structural: `true`
- grounding_reason: `structural_constraint_applied`
- chains: `3`
- edge_counts: `[1, 1, 1]`
- citation_types: `{decision:2, evidence:3}`

### 3) what changed in the structural sync pipeline
- selected_route: `why`
- intent_class: `what_changed`
- used_retry: `false`
- causal_intent flag: `false`
- selected_has_structural: `true`
- grounding_reason: `non_causal_query`
- chains: `3`
- edge_counts: `[1, 1, 1]`
- citation_types: `{evidence:2, decision:2}`

### 4) summarize the immutable causal sync work
- selected_route: `why`
- intent_class: `remember`
- used_retry: `false`
- causal_intent flag: `false`
- selected_has_structural: `true`
- grounding_reason: `non_causal_query`
- chains: `3`
- edge_counts: `[1, 1, 1]`
- citation_types: `{decision:2, evidence:2}`

### 5) remember the retrieval hardening work
- selected_route: `remember`
- intent_class: `remember`
- used_retry: `true`
- causal_intent flag: `false`
- selected_has_structural: `false`
- grounding_reason: `non_causal_query`
- chains: `1`
- edge_counts: `[0]`
- citation_types: `{outcome:3}`

### 6) recall what we changed in memory reason
- selected_route: `why`
- intent_class: `what_changed`
- used_retry: `false`
- causal_intent flag: `false`
- selected_has_structural: `true`
- grounding_reason: `non_causal_query`
- chains: `3`
- edge_counts: `[1, 1, 1]`
- citation_types: `{evidence:2, decision:2}`

### 7) why did we make promotion agent-authoritative?
- selected_route: `why`
- intent_class: `causal`
- used_retry: `false`
- causal_intent flag: `true`
- selected_has_structural: `true`
- grounding_reason: `structural_constraint_applied`
- chains: `3`
- edge_counts: `[1, 1, 1]`
- citation_types: `{decision:2, evidence:2}`

### 8) what caused the promotion inflation episode
- selected_route: `why`
- intent_class: `causal`
- used_retry: `false`
- causal_intent flag: `false`  
  ⚠️ mismatch vs intent_class (likely classifier inconsistency)
- selected_has_structural: `true`
- grounding_reason: `non_causal_query`
- chains: `3`
- edge_counts: `[1, 1, 1]`
- citation_types: `{evidence:3, decision:3}`

### 9) what date did we do the sync work?
- selected_route: `why`
- intent_class: `when`
- used_retry: `false`
- causal_intent flag: `false`
- selected_has_structural: `true`
- grounding_reason: `non_causal_query`
- chains: `3`
- edge_counts: `[1, 1, 1]`
- citation_types: `{decision:2, evidence:2}`

### 10) what do you remember about graph archive retrieval
- selected_route: `why`
- intent_class: `remember`
- used_retry: `false`
- causal_intent flag: `false`
- selected_has_structural: `true`
- grounding_reason: `non_causal_query`
- chains: `3`
- edge_counts: `[1, 1, 1]`
- citation_types: `{evidence:1, decision:3}`

## Key Findings

1. Structural chain selection is strong in this suite (9/10 queries).
2. One recall-style query (`remember the retrieval hardening work`) still returns non-structural chain output.
3. There is one intent classification inconsistency (`what caused ...`) where `intent_class=causal` but `grounding.causal_intent=false`.

## Recommended Follow-up

- Unify causal intent detection logic to remove classification mismatch.
- Keep current structural constraint behavior; it is materially improving grounded outputs.
