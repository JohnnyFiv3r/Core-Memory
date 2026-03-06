# PydanticAI Troubleshooting

Status: Canonical

## Common issues

### 1) Finalized-turn memory not appearing
Check:
- write path actually calls `emit_turn_finalized(...)`
- `session_id` and `turn_id` are stable
- processing pipeline is running as expected

### 2) Runtime memory answers feel weak
Check:
- bead metadata quality
- `warnings`
- `grounding.reason`
- `confidence`
- `next_action`

### 3) Transcript vs memory disagreement
Since PydanticAI runs in-process, it is easy to accidentally over-rely on current local context.

Guideline:
- transcript for immediate/verbatim truth
- Core Memory for durable cross-session/project truth

### 4) Silent drift in direct function usage
Preferred path is `memory.execute` as the canonical facade. Avoid building too much logic on lower-level helper functions unless necessary.
