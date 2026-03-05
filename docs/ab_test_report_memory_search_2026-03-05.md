# A/B Test Report — memory_reason vs typed memory_search

Date: 2026-03-05 (UTC)
Schema: memory_search_ab_compare.v1
Total queries: 20

## Summary Metrics
- A_ok_rate: **1.0**
- B_ok_rate: **1.0**
- B_anchor_presence_rate: **0.95**
- B_confidence_high_rate: **0.45**
- B_warning_rate: **0.05**
- A_non_causal_why_rate: **0.0**

## Acceptance Thresholds
- B_ok_rate >= 1.0 -> PASS
- B_anchor_presence_rate >= 0.6 -> PASS
- B_confidence_high_rate >= 0.4 -> PASS
- B_warning_rate <= 0.5 -> PASS

## Recommendation
- **promote typed path for controlled rollout (remember/what_changed/when first)**

## Per-query Results
| Intent | Query | A route | A results | A chains | B results | B chains | B conf | B next | B anchor | B warnings |
|---|---|---|---:|---:|---:|---:|---|---|---:|---|
| causal | when we had almost all promoted beads what happened | why | 4 | 3 | 0 | 0 | low | ask_clarifying | True |  |
| causal | what caused the promotion inflation episode | why | 5 | 3 | 0 | 0 | low | ask_clarifying | True |  |
| causal | why did compaction get starved during promotion | why | 3 | 2 | 0 | 0 | low | ask_clarifying | True |  |
| causal | promotion blow-up what happened | why | 4 | 3 | 0 | 0 | low | ask_clarifying | True |  |
| causal | remind me what went wrong when everything got promoted | remember | 3 | 1 | 0 | 0 | low | ask_clarifying | True |  |
| causal | why did we move to candidate-first promotion | why | 5 | 3 | 0 | 0 | low | ask_clarifying | True |  |
| causal | why is promotion agent-authoritative now | why | 5 | 3 | 0 | 0 | low | ask_clarifying | True |  |
| causal | what was the rationale for candidate-only promotion | why | 4 | 3 | 0 | 0 | low | ask_clarifying | True |  |
| causal | why did we stop auto promoting everything | why | 4 | 3 | 8 | 0 | medium | broaden | False | no_strong_anchor_match_free_text_mode |
| causal | explain why candidate gate became required | why | 4 | 3 | 0 | 0 | low | ask_clarifying | True |  |
| what_changed | what changed in the structural sync pipeline | remember | 3 | 1 | 8 | 0 | high | answer | True |  |
| what_changed | what did we implement for associations links edges graph | remember | 3 | 1 | 8 | 0 | high | answer | True |  |
| what_changed | summarize the immutable causal sync work | remember | 3 | 1 | 8 | 0 | high | answer | True |  |
| what_changed | what changed in the link edge graph sync | remember | 3 | 1 | 8 | 0 | high | answer | True |  |
| what_changed | quick recap of structural pipeline updates | remember | 3 | 1 | 8 | 0 | medium | broaden | True |  |
| remember | remember the retrieval hardening work | remember | 3 | 1 | 8 | 0 | high | answer | True |  |
| remember | recall what we changed in memory reason | remember | 3 | 1 | 8 | 0 | high | answer | True |  |
| remember | what do you remember about graph archive retrieval | remember | 3 | 1 | 8 | 0 | high | answer | True |  |
| remember | memory reason retrieval updates | remember | 3 | 1 | 8 | 0 | high | answer | True |  |
| remember | remind me what we shipped for graph+archive recall | remember | 3 | 1 | 5 | 0 | high | answer | True |  |