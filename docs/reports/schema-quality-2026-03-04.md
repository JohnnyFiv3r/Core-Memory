# Schema Quality Report (2026-03-04 UTC)

- Total beads: 789
- Status counts: {'promoted': 728, 'superseded': 10, 'open': 51}
- Type counts: {'decision': 204, 'outcome': 15, 'lesson': 9, 'goal': 3, 'association': 1, 'context': 483, 'evidence': 64, 'tool_call': 2, 'checkpoint': 6, 'failed_hypothesis': 1, 'design_principle': 1}

## Validation warnings (warn-first mode)

No validation warnings currently stored.

## Promotion gate blocks (open high-value beads)

{'decision_missing_because_and_evidence_or_detail': 18, 'outcome_invalid_result': 14, 'outcome_missing_link_or_evidence': 14, 'lesson_missing_because': 1}

## Actions taken in this pass

- Added canonical migration decision bead for OpenClaw-only -> multi-orchestrator transition.
- Deduplicated repeated AI Voice contamination cluster by marking later duplicates as superseded.
- Tightened sidecar generation to emit richer outcome/evidence fields (bounded).