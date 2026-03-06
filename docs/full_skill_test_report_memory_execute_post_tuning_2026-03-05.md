# Full Skill-Based Test Report — memory.execute (Post-Tuning)

Status: Historical Snapshot
See also:
- `docs/index.md`
- `docs/canonical_surfaces.md`
- `docs/integrations/shared/validation-common.md`


Date: 2026-03-05 (UTC)
Scope: Same 15 test queries, unified memory skill after confidence/next-action tuning.

## Summary
- count: **15**
- ok_rate: **1.0**
- non_empty_results_rate: **1.0**
- anchor_presence_rate: **0.9333**
- confidence_high_rate: **1.0**
- warning_rate: **0.0667**
- causal_grounding_achieved_rate: **1.0**
- answerable_rate_non_causal: **1.0**

## Per-query Results
| Intent | Query | Results | Chains | Confidence | Next Action | Anchor | Grounding Required | Grounding Achieved | Grounding Reason | Warnings |
|---|---|---:|---:|---|---|---:|---:|---:|---|---|
| remember | promotion inflation | 3 | 0 | high | answer | True | False | False | not_required |  |
| remember | what was that thing where everything got promoted | 3 | 0 | high | answer | True | False | False | not_required |  |
| causal | promotion blow-up what happened | 4 | 3 | high | answer | True | True | True | grounded_via_reasoner |  |
| causal | why did compaction get starved during promotion | 2 | 1 | high | answer | True | True | True | grounded_via_reasoner |  |
| causal | candidate-only promotion rationale | 2 | 1 | high | answer | True | True | True | grounded_via_reasoner |  |
| causal | why did we stop auto promoting everything | 8 | 3 | high | answer | False | True | True | grounded_via_reasoner | no_strong_anchor_match_free_text_mode |
| causal | agent authoritative promotion why | 4 | 3 | high | answer | True | True | True | grounded_via_reasoner |  |
| what_changed | what changed in the link edge graph sync | 8 | 0 | high | answer | True | False | False | not_required |  |
| when | quick recap of structural pipeline updates | 8 | 0 | high | answer | True | False | False | not_required |  |
| remember | immutable causal sync summary | 7 | 0 | high | answer | True | False | False | not_required |  |
| remember | remember retrieval hardening work | 8 | 0 | high | answer | True | False | False | not_required |  |
| when | memory reason retrieval updates | 8 | 0 | high | answer | True | False | False | not_required |  |
| remember | remind me what we shipped for graph+archive recall | 5 | 0 | high | answer | True | False | False | not_required |  |
| when | when did we do sync-structural strict apply | 8 | 0 | high | answer | True | False | False | not_required |  |
| what_changed | what changed in memory reason tool | 8 | 0 | high | answer | True | False | False | not_required |  |