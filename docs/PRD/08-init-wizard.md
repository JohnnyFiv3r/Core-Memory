# PRD: `core-memory init` Wizard + `core-memory doctor` Expansion

**Phase:** 8
**Status:** Not started
**Prerequisite:** Phase 6 complete (`BackendCapabilities`, config-driven `create_backend`)

---

## Problem

`core-memory init` exists as a CLI subcommand but only initializes the `.beads/` and
`.turns/` directories. It does not help the user choose or configure a backend, integration,
or memory behavior. `core-memory doctor` runs basic store health checks but does not verify
retrieval, vector search, graph traversal, or dreamer state.

The result: every new user has to figure out backend selection from docs and env vars
manually. The repo's external reputation is that Core Memory "builds a search datastore"
when the intended message is "Core Memory is the causal memory layer; the backend is
pluggable."

---

## Current state

| Component | Status | Notes |
|-----------|--------|-------|
| `cli.py`: `setup init` subcommand | Exists | Creates directories only |
| `cli.py`: `setup doctor` subcommand | Exists | Basic store health checks |
| `cli_compat.py`: legacy `setup init/doctor` rewrites | Exists | Compat shim |
| Config file (`~/.core-memory/config.yaml`) | Missing | No config file today |
| `create_backend` reads config file | Missing | Reads env var only |
| Doctor: vector search check | Missing | |
| Doctor: graph traversal check | Missing | |
| Doctor: dreamer status | Missing | |

---

## Success criteria

1. `core-memory init` (interactive) or `core-memory init --preset <name>` (non-interactive)
   produces a valid config file at `.core-memory.yaml` (project-local) or
   `~/.core-memory/config.yaml` (user-global, `--global` flag).
2. `create_backend()` reads the config file; env vars override config file values.
3. `core-memory doctor` outputs a structured JSON report covering: storage backend
   reachability, vector search, graph traversal (if applicable), transcript hydration,
   and dreamer process state.
4. The wizard does not crash if the user picks Neo4j but Neo4j is not installed — it
   prints install instructions and writes the config anyway.
5. Running `core-memory init` twice is idempotent: it does not overwrite an existing
   config without `--force`.

---

## Scope

**In:**
- `core-memory init` interactive wizard with `--preset` and `--global` flags
- Config file format (`~/.core-memory/config.yaml` / `.core-memory.yaml`)
- `create_backend()` reads config file
- `core-memory doctor` expanded JSON output
- Preset definitions (see below)

**Out:**
- GUI or web-based setup
- Remote config management
- Backend migration tools (separate workstream)
- Changes to recall semantics or memory behavior defaults

---

## Config file format

```yaml
# .core-memory.yaml (project-local) or ~/.core-memory/config.yaml (user-global)
backend: neo4j            # json | sqlite | postgres | neo4j | custom
vector_backend: auto      # auto | local-faiss | pgvector | none

neo4j:
  uri: bolt://localhost:7687
  username: neo4j
  password: ""            # prefer env var CORE_MEMORY_NEO4J_PASSWORD

postgres:
  dsn: ""                 # prefer env var CORE_MEMORY_POSTGRES_DSN

memory:
  rolling_window_tokens: 4000
  max_beads: 40
  dreamer: true
  transcript_grounding: true

integration: mcp          # mcp | openclaw | pydanticai | http | none
```

`create_backend()` merges in this order: hardcoded defaults < config file < env vars.

---

## Presets

| Preset | Backend | Vector | Notes |
|--------|---------|--------|-------|
| `local` | `json` | `local-faiss` | No dependencies. Good for dev/demo. |
| `sqlite` | `sqlite` | `local-faiss` | Single-file DB, better indexing, no server |
| `postgres` | `sqlite` (local) + pgvector | `pgvector` | Production simple install |
| `neo4j` | `neo4j` | `neo4j` (or faiss) | Graph-native traversal. Recommended for causal inspection. |

`neo4j` is the recommended preset for users who want causal chain visibility.
`local` is the recommended preset for dev, CI, and demo environments.

---

## Wizard interaction (interactive mode)

```
$ core-memory init

Core Memory setup
-----------------

Install type:
  1. Local/dev    — JSONL files, no extra dependencies [default]
  2. SQLite       — single-file DB, better query indexing
  3. Postgres     — pgvector-backed, recommended for production deployments
  4. Neo4j        — graph-native traversal (recommended if causal chain inspection matters)
  5. Custom       — configure manually

> 4

Neo4j connection
  URI [bolt://localhost:7687]:
  Username [neo4j]:
  Password (leave blank to use CORE_MEMORY_NEO4J_PASSWORD env var):

Runtime integration:
  1. MCP server (Claude Code, Cursor, etc.)
  2. OpenClaw
  3. PydanticAI
  4. HTTP/webhook
  5. None / configure later

> 1

Memory behavior (press Enter to accept defaults):
  Rolling window size [4000 tokens]:
  Dreamer background process [on]:
  Transcript grounding [on]:

Writing .core-memory.yaml ... done
Run `core-memory doctor` to verify your setup.
```

---

## `core-memory doctor` expanded output

```json
{
  "storage": {
    "backend": "neo4j",
    "status": "ok",
    "detail": "bolt://localhost:7687 connected"
  },
  "vector_search": {
    "backend": "local-faiss",
    "status": "ok",
    "index_size": 1284,
    "dimension": 384
  },
  "graph_traversal": {
    "available": true,
    "backend": "neo4j",
    "status": "ok",
    "test": "3-hop reachability passed"
  },
  "transcript_hydration": {
    "status": "ok",
    "turns_dir": ".turns/",
    "count": 312
  },
  "dreamer": {
    "status": "not_running",
    "hint": "Start with: core-memory dreamer start"
  },
  "rolling_window": {
    "last_flush_ms": 280,
    "token_budget": 4000,
    "status": "ok"
  }
}
```

Each component has a `status` of `ok`, `warning`, or `error`, plus a `hint` field on
non-ok states. The command exits with code 0 if all are `ok`, 1 if any are `error`.

---

## Implementation tasks

1. **Config file reader** — `core_memory/config/settings.py`. Reads `.core-memory.yaml`
   (project-local, walks up from cwd) and `~/.core-memory/config.yaml` (user-global),
   merges with env vars. Used by `create_backend()`.

2. **`create_backend()` update** — Check for config file before env var fallback. Add
   `"neo4j"` and `"postgres"` cases using config values.

3. **Wizard implementation** — `core_memory/cli_handlers_setup.py` (new file following
   the existing `cli_handlers_*.py` pattern). `init_command(args)` function. Interactive
   prompts via `input()` with defaults. `--preset` bypasses prompts. `--global` writes
   to home directory.

4. **Doctor expansion** — Update `core_memory/cli_handlers_ops.py` `doctor_command` to
   call the backend's `capabilities()` and run probes for each tier. Output structured
   JSON.

5. **Tests** — `tests/test_init_wizard.py` covering: preset mode (no prompts), config
   file written correctly, idempotent (second run no-ops without `--force`), env var
   override of config file value.
