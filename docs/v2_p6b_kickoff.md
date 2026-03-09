# V2-P6B Kickoff (Semantic Closure + Cleanup)

Status: Planned
Purpose: finalize semantic architecture and retire transitional debt after P6A cutover stability.

## Objective
- strengthen association subsystem semantics
- close long-term association bead-type policy
- finalize SpringAI framing posture
- retire deprecated legacy paths where safe

## Step plan (5)
1. Association pass strengthening design + implementation ✅
2. Association bead-type long-term closure decision implementation ✅
3. SpringAI framing finalization (compat-preserving) ✅
4. Legacy path retirement pass ✅
5. Full sweep + P6B closeout

## Precondition
P6A must be complete and stable before P6B execution.

## Step 1 completion notes
- Strengthened association subsystem pass in `core_memory/association/pass_engine.py`:
  - session-relative weighting explicitly prioritized
  - causal cue overlap scoring added
  - relationship typing can emit `supports` under causal/session conditions
- Preserved deterministic ordering + bounded top-k behavior
- Added regression coverage:
  - `tests/test_association_pass_strengthened.py`
  - validates session-relative ranking and causal relationship typing behavior

## Step 2 completion notes
- Closed long-term association bead-type policy with decisive implementation:
  - policy updated to `edge_primary_explicit_bead_only`
- Updated schema policy constant:
  - `core_memory/schema.py::ASSOCIATION_TYPE_POLICY`
- Enforced default behavior in store write path:
  - implicit `type="association"` bead creation now requires explicit flag
  - compatibility override available via `CORE_MEMORY_ALLOW_IMPLICIT_ASSOCIATION_BEAD=1`
- Updated ADR with superseding decision details:
  - `docs/adr_association_type_policy.md`
- Expanded policy tests:
  - `tests/test_association_type_policy.py`
  - verifies policy value, default enforcement, and compat override behavior

## Step 3 completion notes
- Finalized SpringAI-first framing while preserving HTTP compatibility:
  - `core_memory/integrations/http/server.py` app title updated to SpringAI bridge-compatible framing
  - `core_memory/integrations/http/__init__.py` now explicitly labeled compatibility ingress
- Updated SpringAI docs landing page with explicit primary/compat entrypoints:
  - `docs/integrations/springai/README.md`
- Extended bridge regression test:
  - `tests/test_springai_bridge.py`
  - verifies bridge framing is reflected in app metadata/title

## Step 4 completion notes
- Applied legacy poller hard-fence in OpenClaw integration layer:
  - `process_pending_memory_events(...)` is now disabled by default
  - explicit opt-in required: `CORE_MEMORY_ENABLE_LEGACY_POLLER=1`
- Updated legacy/deprecation docs:
  - `docs/v2_deprecation_inventory.md`
  - `docs/v2_legacy_resolution_summary.md`
- Added/updated regression coverage:
  - `tests/test_legacy_poller_fence.py`
  - `tests/test_trigger_authority_markers.py`
  - `tests/test_openclaw_integration.py` (legacy-poller path now explicitly env-enabled)
