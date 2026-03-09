# V2 Deprecation Inventory

Status: Canonical inventory (V2-P1)
Purpose: track legacy paths/components that will be deprecated or removed after canonical V2 cutover.

## Inventory schema
- Component/path:
- Current role:
- Why legacy:
- Canonical replacement:
- Removal preconditions:
- Planned phase:
- Status: active | deprecated | removed
- Notes:

---

## Initial entries

### 1) Sidecar-authority orchestration path
- Component/path: sidecar-led event authority paths (`core_memory/sidecar*`, poller authority semantics)
- Current role: trigger/process helper and compatibility event processing
- Why legacy: authority should be canonical in-process flush/turn path
- Canonical replacement: V2 canonical trigger enforcement + flush authority path
- Removal preconditions: V2-P2 trigger enforcement tests green + operator sanity path verified
- Planned phase: deprecated in V2-P2, remove/further reduce in V2-P5
- Status: deprecated
- Notes: retained as compatibility wrapper; explicitly non-authoritative

### 2) Legacy compatibility wrappers beyond canonical flow
- Component/path: any wrapper routing that duplicates canonical orchestrator behavior
- Current role: historical compatibility
- Why legacy: duplicate authority paths increase ambiguity
- Canonical replacement: single canonical orchestration path + minimal admin fail-safe
- Removal preconditions: no mainline callers depend on legacy wrapper path
- Planned phase: review in V2-P5
- Status: deprecated
- Notes: maintain only where operationally required; no authority semantics

### 3) Draft/superseded roadmap references
- Component/path: pre-v2 execution references used as active plan
- Current role: historical context
- Why legacy: v2 plan is canonical for current program
- Canonical replacement: `docs/transition_roadmap_v2.md` + `docs/v2_execution_plan.md`
- Removal preconditions: v2 fully adopted
- Planned phase: V2-P6 closeout cleanup
- Status: deprecated
- Notes: keep historical docs marked as superseded/historical rather than delete abruptly
