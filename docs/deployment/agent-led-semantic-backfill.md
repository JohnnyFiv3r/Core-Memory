# Agent-led semantic backfill runbook

Use the governed maintenance actions to fill pending semantic turns or append
richer interpretations of legacy beads. The workflow is deliberately
dry-run-first and append-only: source beads and external evidence anchors are
never rewritten.

## Preconditions

- The tenant is backed up or copied using the host's normal storage procedure.
- Hosted copied and live deployments set
  `CORE_MEMORY_MAINTENANCE_ENVIRONMENT=copied_tenant` or `live_tenant`
  respectively. Core Memory rejects a request environment that does not match
  the configured store, preventing a live root from being mislabeled as a copy.
- A delegated `turn_memory_authoring` runtime is configured and can return the
  full `agent_authored_updates.v1` contract.
- The operator has `admin_repair`, `reauthor_memory`, or
  `retry_pending_semantic` authority and supplies a stable actor identity.
- Apply requests use a unique idempotency key. Reusing that key with a changed
  source selection returns `idempotency_key_conflict`.

## 1. Establish the baseline

Call `maintain` with `action="semantic_backfill_report"`. Save the complete
response. It reports pending count and age plus separate `legacy`,
`v1_authored`, and `backfilled` cohorts. Each cohort includes bead count,
source-anchor count, retrieval framing, claims, semantic keys, judged semantic
relationships, causal edges, and relationship counts.

The legacy cohort keeps historical retrieval eligibility. Do not compare its
raw eligible count to v1 writes without retaining this cohort split.

## 2. Preview a copied tenant

For thin-bead enrichment, call:

```json
{
  "action": "reauthor_memory",
  "scope": {"environment": "copied_tenant"},
  "targets": {"bead_ids": ["bead-..."], "limit": 50},
  "authority": {"actor": "operator@example.com", "allowed_authority": ["admin_repair"]},
  "dry_run": true,
  "apply": false
}
```

Use `targets.sweep=true` for a bounded automatic selection. The default keeps
only thin legacy rows and excludes v1-authored or already backfilled beads.
Set `thin_only=false` or `include_v1=true` only for an explicitly reviewed
maintenance batch. A `decision.revision_type` of `correction` or `reversal`
records an explicit revision link; ordinary enrichment records derivation.

For unresolved finalized turns, call `retry_pending_semantic` with one
`scope.session_id` and `scope.turn_id`, or use a bounded
`targets.sweep=true`. Preview never invokes a model or writes state.

Review selected sources, missing IDs, pending age, and baseline cohorts before
apply.

## 3. Apply to the copied tenant

Repeat the exact request with `dry_run=false`, `apply=true`, and a stable
`idempotency_key`. The delegated author receives the complete typed contract
and bounded source context. Core Memory persists the primary row through the
canonical turn-write path, resolves any bounded derived companions, and only
then schedules agent-judged association coverage.

Save the complete successful response. It is the copied-tenant validation
receipt required for live apply. Confirm:

- every expected source was examined;
- source bead snapshots are unchanged;
- primary and derived write counts are plausible;
- validation failures are understood;
- pending age decreased for retry batches;
- association decisions, `no_link`, pending-judge, and edge counts are visible;
- the `backfilled` cohort gained retrieval facts, claims, or semantic keys
  without collapsing the legacy/v1 cohort split.

## 4. Apply to the live tenant

Use the same action and source selection with
`scope.environment="live_tenant"`. Put the successful copied-tenant result in
`decision.copied_tenant_validation_receipt`. Core Memory rejects live apply if
that receipt is absent, unsuccessful, from a different action, or not marked as
an applied `copied_tenant` maintenance receipt.

Use a new live-tenant idempotency key. Save the live receipt and the append-only
`.beads/events/semantic-maintenance.jsonl` audit record.

## 5. Verify and rerun safely

Run `semantic_backfill_report` again and compare the same cohorts. Reusing an
apply idempotency key with the same request returns the original result without
another model call or write. A changed request with the same key is rejected.
Failures do not mutate their source and do not trigger causal coverage; repair
the delegated author configuration or payload and retry with a new key.

## Retired seed-backfill route

`POST /v1/memory/hygiene/seed-backfill` remains a read-only census during its
compatibility window. Its former `apply=true` behavior directly rewrote stored
beads and auto-applied semantic outcomes, so apply now returns
`seed_quality_backfill_apply_retired` with guidance to this workflow.
