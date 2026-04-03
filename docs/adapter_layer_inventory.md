# Adapter Layer Inventory

Status: Canonical clarification note
Purpose: distinguish thin adapter surfaces from canonical behavior/skill semantics.

## `core_memory/tools/memory.py`
Role: **Adapter layer** (thin callable shim), not agent skill semantics.

### What it is
- A stable Python entrypoint that delegates to canonical memory skill/runtime functions.
- A compatibility boundary for integrations that call `search`, `trace`, and `execute`.
- A feature-flag gate for `memory.execute` rollout controls.

### What it is NOT
- Not the canonical decision policy for agent tool-routing.
- Not the skill instruction layer for OpenClaw usage guidance.
- Not the read-side retrieval/reasoning implementation owner.

### Delegation map
- `search(...)` -> `memory_search_typed(...)`
- `trace(...)` -> `memory_trace(...)`
- `execute(...)` -> `memory_execute(...)` (+ env flag guards)

### Refactor guidance
- Keep as a stable adapter unless/until a reviewed migration supersedes it.
- Any future consolidation must preserve tool contracts and behavior.
- Skill semantics should live in docs/instructions, not this adapter module.
