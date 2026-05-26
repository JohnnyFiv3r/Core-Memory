# PRD: `core-memory init` Wizard + `core-memory doctor` Expansion

**Phase:** 8
**Prerequisite:** Phase 6 complete (`BackendCapabilities`, config-driven `create_backend`)

---

## Phase 8a — Completed (2026-05-26)

**What shipped:**

| Task | File | Status |
|------|------|--------|
| Layered config reader (defaults < user-global < project-local < env vars) | `core_memory/config/settings.py` | ✓ |
| `core-memory setup init` wizard with `--preset`, `--global`, `--force` | `core_memory/cli_handlers_setup.py` | ✓ |
| Creates `.beads/` + `.turns/` on init, writes `.core-memory.yaml` | `cli_handlers_setup.py:init_command` | ✓ |
| Idempotent init (skips without `--force`) | `cli_handlers_setup.py:init_command` | ✓ |
| `expanded_doctor` — 6-tier JSON report (storage/vector/graph/dreamer/rolling-window/transcript) | `cli_handlers_setup.py:expanded_doctor` | ✓ |
| Exits 1 on any `"error"` tier | `cli_handlers_setup.py:doctor_command` | ✓ |
| `ops doctor` / top-level `doctor` use `expanded_doctor` | `cli.py` | ✓ |
| 17 new tests | `tests/test_init_wizard.py` | ✓ |

**Known gaps in 8a (addressed in 8b):**

- Wizard asks about storage backend first, not use case. Users must understand the architecture before choosing.
- `--preset` names expose technology choices (`neo4j`, `sqlite`) instead of intents (`mcp`, `production`).
- Doctor output is JSON-only. No human-readable mode with actionable hints.
- Kuzu is framed as an optional graph upgrade, not the zero-config embedded default it actually is.
- No doctor profiles — a local user sees the same warning severity as a production user.
- No `core-memory demo` command for first-run experience.
- No `core-memory config show/set/validate` for inspection.

---

## Phase 8b — Extended Scope

### Problem restatement

The 8a wizard still asks the wrong first question. "Choose your storage backend" exposes the
architecture before the user has felt the product. The correct first question is "What are you
building?" — and the CLI chooses the stack.

Additionally, Kuzu is **not** an optional graph capability. It is the embedded default — zero
dependencies, zero ops, no configuration required. A local user who never touches graph config
gets full causal traversal via Kuzu. Treating graph as an "optional upgrade" in doctor output
is actively misleading. The 8b doctor must show Kuzu as a default-present capability, not a
missing one.

---

### 8b-1 — Mode-based wizard (`--mode` replaces `--preset`)

The wizard's first question becomes "What are you setting up Core Memory for?" Modes map to
**user intent**, not storage backend. The CLI resolves the stack from the mode.

#### Mode definitions

| Mode | Storage | Graph | Vector | Integration | Intended use |
|------|---------|-------|--------|-------------|--------------|
| `local` | `json` | `kuzu` (embedded) | `local-faiss` | `none` | Dev, CI, trying it out |
| `mcp` | `json` | `kuzu` (embedded) | `local-faiss` | `mcp` | Claude Desktop / Cursor / MCP clients |
| `app` | `sqlite` | `kuzu` (embedded) | `local-faiss` | `none` | PydanticAI / OpenClaw / custom frameworks |
| `production` | `postgres` | `neo4j` | `pgvector` | `none` | Durable, graph-native, inspectable |

`local` is the default. `kuzu` is the graph default for all non-production modes — it is embedded
and requires no configuration.

#### Wizard interaction (mode-based)

```
$ core-memory init

Core Memory setup
-----------------

What are you setting up Core Memory for?

  1. Local dev / trying it out   [default]
  2. MCP server (Claude, Cursor, etc.)
  3. App integration (PydanticAI, OpenClaw, etc.)
  4. Production service
  5. Custom / advanced

> 2

MCP configuration
  Generate MCP server config? [yes]:

Memory behavior (press Enter to accept defaults):
  Rolling window size [4000 tokens]:
  Dreamer [on]:
  Transcript grounding [on]:

Writing .core-memory.yaml ... done
Run `core-memory doctor --profile mcp` to verify your setup.
```

Production mode prompts for Postgres DSN and Neo4j URI. All other modes skip those prompts.

#### `--mode` flag (non-interactive)

```bash
core-memory init --mode local
core-memory init --mode mcp
core-memory init --mode production --global
```

`--preset` is kept as a deprecated alias for `--mode` for one release cycle.

#### Config file: add `mode` field

```yaml
mode: mcp                 # local | mcp | app | production | custom

backend: json
vector_backend: local-faiss
graph_backend: kuzu       # kuzu is the zero-config default for local/mcp/app

integration: mcp

memory:
  rolling_window_tokens: 4000
  max_beads: 40
  dreamer: true
  transcript_grounding: true
```

---

### 8b-2 — Doctor profiles (`--profile`)

Doctor severity depends on intended use. A local developer should not see a red error for
"Neo4j not configured." A production deployment should not silently skip that check.

Profile is auto-detected from the config file's `mode` field if `--profile` is not given.

#### Profile severity matrix

| Check | `local` | `mcp` | `app` | `production` |
|-------|---------|-------|-------|--------------|
| Storage writable | error | error | error | error |
| Graph (Kuzu) available | ok — shown as present | ok | ok | ok |
| Graph (Neo4j) durable | not shown | not shown | not shown | error |
| Vector search configured | info | info | warning | error |
| MCP server starts | not shown | error | not shown | not shown |
| Dreamer running | info | info | info | warning |
| Postgres reachable | not shown | not shown | not shown | error |
| Rolling window | ok | ok | ok | error if stale |

A check that is "not shown" for a profile does not appear in the report at all — it is not
shown as an error, a warning, or an ok. Profile-irrelevant checks are invisible.

#### Human-readable doctor output (default)

```
$ core-memory doctor

Core Memory Doctor  [profile: mcp]

✓ Storage        .beads/ writable, 3 beads
✓ Graph          Kuzu (embedded, zero-config)
✓ Rolling window 0 records (new store)
⚠ Embeddings     Not configured
                 Impact: semantic recall will use BM25 fallback
                 Fix:    core-memory config set vector_backend local-faiss
✗ MCP server     Not started
                 Impact: Claude/Cursor cannot connect
                 Fix:    core-memory mcp install claude

Next step: core-memory mcp install claude
```

Every non-ok check has exactly three parts:
- `Impact:` — what breaks or degrades
- `Fix:` — the exact command that resolves it

JSON output via `--json` flag. Human-readable is the default.

#### `--profile` flag

```bash
core-memory doctor                        # auto-detect from config
core-memory doctor --profile local
core-memory doctor --profile production
core-memory doctor --json                 # machine-readable (current behavior)
```

---

### 8b-3 — `core-memory config` subcommand

Inspection and CRUD surface for the resolved config. Power-user commands; not needed for
first-run.

```bash
core-memory config show          # resolved config with provenance labels
core-memory config validate      # check for contradictions / missing required fields
core-memory config set key value # set one key in project-local .core-memory.yaml
```

#### `config show` output

```
$ core-memory config show

Resolved configuration  (project-local wins over user-global; env vars win over all)

  mode:            mcp              [.core-memory.yaml]
  backend:         json             [.core-memory.yaml]
  graph_backend:   kuzu             [default]
  vector_backend:  local-faiss      [.core-memory.yaml]
  integration:     mcp              [.core-memory.yaml]
  memory.dreamer:  true             [default]

Sources searched:
  project-local:  .core-memory.yaml
  user-global:    ~/.core-memory/config.yaml  (not found)
  env vars:       none relevant set
```

#### `config set`

```bash
core-memory config set graph_backend neo4j
core-memory config set memory.dreamer false
```

Writes to `.core-memory.yaml` (project-local). Adds the key if missing, updates if present.
Does not overwrite unrelated keys.

---

### 8b-4 — `core-memory demo`

First-run experience command. Writes synthetic beads, fires a recall, prints the result. No
setup required — uses the current store root.

```
$ core-memory demo

Core Memory demo
----------------

Writing 3 synthetic beads...
  ✓ decision: "Chose Python over Go for simplicity"
  ✓ context:  "This is a demo project evaluating memory systems"
  ✓ insight:  "Go has better concurrency but Python has the ecosystem"

Running recall: "why did we choose Python?"

Result (1.4s):
  decision: "Chose Python over Go for simplicity"
  → caused by: context + insight (2-hop causal chain)
  → confidence: 0.87

Memory is working. Run `core-memory doctor` for full setup status.
```

Exits 0 always (demo beads are written to a temporary session, not promoted).

---

## Full success criteria (8a + 8b)

1. `core-memory init` (interactive) or `core-memory init --mode <name>` (non-interactive)
   produces a valid config file. ✓ **8a**
2. Config file read with 4-level precedence; env vars win. ✓ **8a**
3. Doctor outputs 6-tier JSON report; exits 1 on any error tier. ✓ **8a**
4. Init is idempotent without `--force`. ✓ **8a**
5. Wizard first question is use-case intent, not storage backend. **8b-1**
6. `--mode` maps intent to stack; Kuzu is included in all non-production modes by default. **8b-1**
7. Doctor auto-detects profile from config; profile gates which checks are errors vs warnings vs hidden. **8b-2**
8. Default doctor output is human-readable with three-part warnings (impact + fix). **8b-2**
9. Kuzu shows as "✓ Graph: Kuzu (embedded)" for local/mcp/app profiles, never a warning. **8b-2**
10. `core-memory config show` displays resolved config with per-key provenance. **8b-3**
11. `core-memory config set key value` updates project-local config non-destructively. **8b-3**
12. `core-memory demo` writes synthetic beads, runs recall, prints result, exits 0. **8b-4**

---

## Scope

**In:**
- Mode-based wizard (`--mode` replacing `--preset`)
- Doctor profiles with severity matrix
- Human-readable doctor output with `Impact:` / `Fix:` per warning
- `core-memory config show/set/validate`
- `core-memory demo`

**Out:**
- GUI or web-based setup
- `--fix` auto-remediation in doctor (tempting but too risky for automated use)
- Backend migration tools (separate workstream)
- Remote config management

---

## Implementation tasks (8b)

### 8b-1 Mode wizard

1. Add `mode` field to `_DEFAULTS` and `_PRESETS` in `cli_handlers_setup.py`.
2. Rename `_PRESETS` keys from `local/sqlite/postgres/neo4j` to `local/mcp/app/production`;
   keep `--preset` as deprecated alias for `--mode`.
3. Rewrite `_interactive_wizard()` to ask use-case intent first. Mode resolves the stack.
   Production mode asks for Postgres DSN + Neo4j URI. All others skip those prompts.
4. Add `mode` to the written YAML.
5. Update tests: `test_mode_local_writes_kuzu_graph_backend`, `test_mode_mcp_writes_integration_mcp`,
   `test_mode_production_prompts_for_neo4j_uri`.

### 8b-2 Doctor profiles

1. Add `DoctorProfile` enum / dataclass: `local | mcp | app | production`.
2. Add `_profile_from_config(root)` — reads mode from settings, maps to profile.
3. Refactor each probe in `expanded_doctor` to accept `profile` and return `None` for
   hidden checks; return `{"status": "info", ...}` for informational-only checks.
4. Add human-readable formatter `_format_doctor_human(report)` — renders checkmarks,
   warnings, errors, and the three-part hint block.
5. `doctor_command` accepts `--profile` arg and `--json` flag. Default: human-readable.
6. Graph probe for local/mcp/app: confirm Kuzu initializes, return `ok` with
   `"backend": "kuzu (embedded)"`. Never return warning for missing Neo4j on these profiles.
7. Update tests: profile severity matrix (local hides neo4j check, production surfaces it),
   human-readable output contains "Impact:" for non-ok checks.

### 8b-3 Config commands

1. Add `config_parser` to `cli.py` with subcommands `show`, `set`, `validate`.
2. Add `config_show_command(args)`, `config_set_command(args)`, `config_validate_command(args)`
   to `cli_handlers_setup.py`.
3. `config show`: calls `load_settings(root)`, re-runs the load with provenance tracking
   (add `load_settings_with_provenance(root)` variant that returns `{key: (value, source)}`).
4. `config set key value`: parses dotted key path, reads existing YAML, updates in-place,
   writes back. Does not touch keys not mentioned.
5. `config validate`: checks for contradictions (e.g. `graph_backend: neo4j` with no `neo4j.uri`)
   and missing required fields for the declared mode.
6. Tests: provenance shows correct source, set is non-destructive, validate catches
   neo4j-without-uri contradiction.

### 8b-4 Demo command

1. Add `demo` top-level subcommand to `cli.py`.
2. Add `demo_command(args)` to `cli_handlers_setup.py`.
3. Write 3 synthetic beads via `MemoryStore.add_bead()`, tag session as `demo`.
4. Run `recall()` with a canned query ("why did we choose Python?").
5. Print human-readable result showing matched beads and causal chain if available.
6. Clean up demo session on exit (delete the 3 beads) or accept `--keep` flag.
7. Tests: demo runs without error on blank store, exits 0, cleans up session.
