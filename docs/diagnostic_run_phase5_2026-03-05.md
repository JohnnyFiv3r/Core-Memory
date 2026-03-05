# Diagnostic Run Report — Phase 5 Explainability Sweep

Date: 2026-03-05 (UTC)
Queries: 15 (messy, user-like variants)

## Summary Metrics
- Anchor hit rate (top5): **0.8667** (13/15)
- Why-route share: **0.3333** (5/15)
- Non-causal why-rate: **0.0000** (0/10)

## Misses (anchor_hit_top5 = false)
- Query: `what was that thing where everything got promoted`
  - route=remember intent_class=remember why_no_anchor_hit=top5_missing_anchor
  - matched_incidents=1 matched_topics=0 penalties=3
- Query: `why did we stop auto promoting everything`
  - route=why intent_class=causal why_no_anchor_hit=no_anchor_matches
  - matched_incidents=0 matched_topics=0 penalties=7

## Per-query Table
| Query | Route | Intent | AnchorHitTop5 | WhyNoAnchorHit | Incidents | Topics | ExpandedNeighbors | Penalties |
|---|---|---|---:|---|---:|---:|---:|---:|
| promotion inflation | remember | remember | True | none | 2 | 1 | True | 2 |
| what was that thing where everything got promoted | remember | remember | False | top5_missing_anchor | 1 | 0 | True | 3 |
| promotion blow-up what happened | why | causal | True | none | 2 | 1 | True | 4 |
| why did compaction get starved during promotion | why | causal | True | none | 2 | 1 | True | 6 |
| candidate-only promotion rationale | why | causal | True | none | 2 | 1 | True | 2 |
| why did we stop auto promoting everything | why | causal | False | no_anchor_matches | 0 | 0 | True | 7 |
| agent authoritative promotion why | why | causal | True | none | 2 | 1 | True | 2 |
| what changed in the link edge graph sync | remember | what_changed | True | none | 0 | 2 | True | 7 |
| quick recap of structural pipeline updates | remember | when | True | none | 0 | 1 | True | 0 |
| immutable causal sync summary | remember | remember | True | none | 0 | 1 | True | 2 |
| remember retrieval hardening work | remember | remember | True | none | 0 | 1 | True | 3 |
| memory reason retrieval updates | remember | when | True | none | 0 | 1 | True | 5 |
| remind me what we shipped for graph+archive recall | remember | remember | True | none | 0 | 2 | True | 3 |
| when did we do sync-structural strict apply | remember | when | True | none | 0 | 1 | True | 7 |
| what changed in memory reason tool | remember | what_changed | True | none | 0 | 1 | True | 5 |

## Notes
- Anchor mapping is now strong overall; remaining misses are alias/topic coverage gaps.
- One miss is `no_anchor_matches` (resolver dictionary gap), one is `top5_missing_anchor` (ranking competition).