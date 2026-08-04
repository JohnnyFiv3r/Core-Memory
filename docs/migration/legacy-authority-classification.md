# Legacy authority classification

Status: PR-00C static inventory contract, extended by PR-00D consolidation

This document defines the read-only code and filesystem inventory delivered by
PR-00C. It inventories legacy authority; it does not approve, import, repair,
rewrite, delete, or switch any authority.

The machine-readable rules are
`core_memory/data/legacy_inventory_classification.v1.json`. Incorrect
classification is corrected by reviewing and versioning that file, then
regenerating affected reports. The scanner never invents a replacement
classification from source content.

## Commands

Static code scan:

```bash
python scripts/inventory_legacy_memory.py scan-code \
  --root /absolute/path/to/repository \
  --include core_memory \
  --include scripts \
  --exclude core_memory/migration \
  --exclude scripts/inventory_legacy_memory.py \
  --source-label repository-static-authorities \
  --tenant-workspace all-workspaces-static \
  --out /absolute/separate/path/code-inventory.json
```

Bounded filesystem scan:

```bash
python scripts/inventory_legacy_memory.py scan-filesystem \
  --root /absolute/path/to/legacy-data \
  --workspace-root /absolute/path/to/repository \
  --source-label local-legacy-store \
  --tenant-workspace tenant-or-workspace-classification \
  --out /absolute/separate/path/filesystem-inventory.json
```

`scan-sql`, `merge`, and `verify` are implemented by PR-00D and documented in
`docs/migration/legacy-consolidated-inventory.md`.

## Read-only boundary

- Roots, includes, source labels, tenant/workspace classification, and outputs
  are explicit.
- Both scanners require absolute roots and refuse `/` and the current user's
  home directory.
- The filesystem scanner refuses the workspace root. Reports cannot be written
  inside a scanned filesystem root.
- A code report may be written elsewhere in the repository only when it is
  outside every explicit code include. It cannot overwrite or enter scanned
  code.
- Python is parsed with `ast`; scanned modules are never imported or executed.
- Filesystem traversal does not follow symlinks. A symlink is recorded as an
  external reference using only a target fingerprint.
- Broad filesystem scans hash SQLite files as handles but never open them.
  PR-00D opens a database only through a separately authorized `scan-sql`
  command with read-only enforcement.
- Malformed and duplicate records are counted without repair. Unsupported
  formats are hashed as opaque artifacts.
- Absolute roots, credential values, DSNs, raw database URLs, and symlink
  targets are excluded from reports. Suspicious source labels are replaced by
  stable redacted labels.

## What the code scanner classifies

The AST scanner records exact paths, owner symbols, lines, node hashes, and
mechanical operations for:

- filesystem readers and writers;
- SQL readers, writers, and unresolved dynamic operations;
- known semantic mutation calls;
- fallback and resolver candidates in semantic domains;
- queue classes and operations;
- store, repository, index, and backend classes/imports;
- environment variables selecting authority, providers, backends, models,
  modes, or fallbacks;
- statically recoverable JSON, JSONL, SQLite, `.beads`, and `SOUL.md` path
  templates.

`resolver_candidate` and `database_operation` are intentionally conservative.
They identify review surfaces without claiming that a dynamic operation is a
writer or that every function named `resolve` determines current truth.

## What the filesystem scanner measures

Every discovered file or symlink receives the common inventory fields:

- redacted source label and tenant/workspace classification;
- relative locator and locator kind;
- authority and provenance classifications;
- record and byte counts;
- minimum and maximum parseable event times;
- SHA-256 content hash;
- parse-failure and duplicate-ID counts;
- recoverability status;
- reader/writer symbol arrays;
- proposed future importer and planned deletion PR.

JSONL is streamed one row at a time. JSON containers use explicit known record
keys such as `beads`, `claims`, `associations`, `jobs`, and `artifacts`.
Content carrying a receipt is classified as `llm_authored_with_receipt` only
when every counted record carries one. Mixed receipt state remains `unknown`.
No frequency, timestamp, filename, or parse success upgrades semantic truth.

## Disposition map

| Legacy surface | Proposed target/importer | Deletion phase |
|---|---|---|
| JSONL, index, and file authority | PR-10A source/bead transforms | PR-10H |
| Turn/capture/write pipeline | PR-10A plus the PR-03 capture path | PR-10I |
| Claims, revisions, and associations | PR-10B transforms | PR-10J |
| Dreamer, goals, storylines, SOUL, artifacts, promotion | PR-10C transforms | PR-10K |
| Recall, search, causal answer, hydration | PR-08 unified RetrievalPipeline | PR-10L |
| Queues, jobs, candidate stores, authority flags | PR-10C/PR-09F/PR-09G | PR-10M |
| SQLite and hosted handles | PR-00D read-only classification first | Domain-specific PR-10 transform |

These are migration and deletion dispositions, not current implementation
claims. All legacy authorities remain active until their later cutover and
deletion gates pass.

## Committed static snapshot

`docs/migration/legacy-static-code-inventory.v1.json` is the public repository
snapshot generated from the PR-00B integration head plus the PR-00C scanner,
excluding the scanner itself.

- 342 Python files scanned.
- 3,076,362 source bytes scanned.
- 899 classified records.
- 0 parse failures.
- 0 unclassified records.
- 107 configuration selectors.
- 288 filesystem reads and 171 filesystem writes.
- 41 database reads/writes/unknown operations.
- 5 semantic fallback candidates and 11 resolver candidates.
- 8 governed semantic writer call sites.
- 228 statically recoverable filesystem authority references.

No private filesystem content or live-store locator is committed. PR-00D
consolidates the public static report with private read-only local reports into
a sanitized authority-level manifest.

## Known limits

Static analysis cannot resolve arbitrary reflection, dynamically constructed
module names, runtime-only path values, or opaque third-party state. Rather
than guess, it records dynamic SQL as `database_operation`, records unresolved
path variables in templates, and leaves data provenance `unknown`. PR-00D
reconciles these surfaces through an explicit known-authority registry and
complete read-only reports for every active authority.

Rollback is a normal code revert. The scanners have no source-state rollback
because they never mutate scanned state.
