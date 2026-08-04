# Consolidated legacy authority inventory

Status: PR-00D read-only database and consolidated inventory contract

PR-00D completes the Stage 0 legacy inventory boundary. It adds SQLite and
PostgreSQL inspection, an explicit registry of known authorities, consolidation,
and completeness verification. It does not import, repair, rewrite, delete, or
switch any authority.

The committed authority registry is
`docs/migration/known-authorities.v1.json`. The sanitized consolidated result is
`docs/migration/legacy-consolidated-inventory.v1.json`. Private source reports
remain outside the repository.

## Commands

Read-only SQLite inventory:

```bash
python scripts/inventory_legacy_memory.py scan-sql \
  --engine sqlite \
  --sqlite-path /absolute/path/to/legacy.sqlite \
  --source-label local-sqlite-authority \
  --tenant-workspace tenant-or-workspace-classification \
  --out /absolute/separate/path/sqlite-report.json
```

Read-only PostgreSQL inventory:

```bash
export INVENTORY_POSTGRES_DSN='<credential-bearing DSN>'
python scripts/inventory_legacy_memory.py scan-sql \
  --engine postgresql \
  --dsn-env INVENTORY_POSTGRES_DSN \
  --schema public \
  --source-label hosted-postgres-authority \
  --tenant-workspace tenant-or-workspace-classification \
  --out /absolute/separate/path/postgres-report.json
```

The DSN is accepted only through the explicitly named environment variable. It
is never accepted as a command-line value, written to a report, or included in
an error. Requested and discovered relations are represented by redacted target
fingerprints and qualified table names.

Consolidation and verification:

```bash
python scripts/inventory_legacy_memory.py merge \
  --registry /absolute/path/to/known-authorities.v1.json \
  --report /absolute/path/to/static-report.json \
  --report /absolute/path/to/private-filesystem-report.json \
  --report /absolute/path/to/sql-report.json \
  --out /absolute/path/to/consolidated-manifest.json

python scripts/inventory_legacy_memory.py verify \
  --manifest /absolute/path/to/consolidated-manifest.json
```

`merge` writes an incomplete manifest and exits nonzero when an active authority
has no report, has multiple reports, is inaccessible, is incomplete, or contains
unclassified records. It also fails for a report that is not declared by the
registry. `verify` checks those gates again and verifies the manifest content
fingerprint.

## SQL read-only enforcement

SQLite scanning requires an explicit absolute file path, rejects symlinks, opens
the database with `mode=ro&immutable=1`, enables and verifies `query_only`, and
uses a read transaction. Because immutable mode cannot incorporate journaled
changes without writing, the scanner refuses a nonempty WAL or rollback journal
instead of returning stale counts. File content, size, modification time, and
sidecar state are checked before and after inspection. The scanner never opens
SQLite handles discovered by a broad filesystem scan; each database must be
separately authorized.

PostgreSQL scanning opens a repeatable-read transaction with `BEGIN ... READ
ONLY` before any catalog or table query and verifies both
`transaction_read_only=on` and `transaction_isolation=repeatable read`. It rolls the
transaction back and closes the connection on success and failure. Catalog,
count, size, duplicate-ID, receipt-coverage, and event-time queries are all
`SELECT`/`SHOW` operations. Connection and query failures expose only the error
class, never the DSN or credential-bearing provider message.

Both engines inventory structure and aggregates, not semantic row content.
Per-table stable hashes cover engine, relation, columns, counts, byte size,
event-time range, duplicate count, and receipt coverage. They are metadata
fingerprints, not truth scores. Receipt coverage may refine provenance but
never upgrades factual truth.

## Known-authority registry

Every registry authority has:

- a stable authority ID and source alias;
- local, SQL, repository, or hosted kind;
- `active`, `not_configured`, or `retired` deployment status;
- required scanner and public/private visibility;
- nonempty authority and provenance classification;
- future importer and mandatory deletion PR;
- structural evidence identifying why the surface is in scope.

An `active` authority must have exactly one complete read-only report. It cannot
be satisfied by a declaration. A `not_configured` or `retired` capability must
be a declaration and cannot be counted as accessible. This prevents support for
a backend from being misreported as a successfully inventoried deployment.

The current registry reconciles repository authority surfaces; primary local
beads and turns; the user-level default store; three local tenant roots; SQLite
and PostgreSQL capability; embedded and server vector/graph projections;
PipeHouse retrieval/SQL surfaces; Zep; and the OpenClaw hosted-capture surface.

## Current sanitized result

The committed manifest records:

- 15 classified authority surfaces;
- 7 active authorities with 7 complete read-only source manifests;
- 8 supported surfaces explicitly recorded as not configured;
- 0 inaccessible authorities;
- 0 unclassified authorities;
- 0 undeclared reports;
- 0 parse failures and 0 duplicate IDs in the active local source reports.

The six private local reports contain 159 inventory records covering 840
logical source records and 3,259,638 bytes. Those reports, raw locators, tenant
IDs, session names, and per-file hashes are not committed. The consolidated
manifest retains only source aliases, aggregate summaries, metadata
fingerprints, classifications, access state, and migration dispositions.

No configured PostgreSQL, SQLite runtime file, Neo4j, Qdrant server, ChromaDB,
PipeHouse endpoint/database, Zep, or hosted-capture endpoint was present in the
explicit Stage 0 environment snapshot. These surfaces are therefore
`not_configured`, not `accessible`. If any becomes configured, its registry row
must change to `active` and verification will require a corresponding complete
read-only report.

## Limits and rollback

Exact row counts can be expensive on large PostgreSQL relations; PR-00D favors
an honest inventory over estimates. SQLite table bytes use the optional
`dbstat` virtual table and report a warning when it is unavailable. Runtime
configuration outside the declared Stage 0 scope cannot be discovered by code
inspection alone and must be added explicitly to the registry before merge.

Rollback is a normal code revert. Source authorities need no rollback because
the inventory operations never mutate them.
