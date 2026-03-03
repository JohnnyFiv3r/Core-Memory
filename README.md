# Core Memory

A deterministic, causal memory system for AI agents: **“beads for memory.”**

Core Memory stores durable **beads** (decisions, lessons, outcomes, evidence, etc.) and explicit links between them, then compacts and injects the most relevant prior memory into the agent context via a **Context Packet**.

![Core Memory overview](docs/assets/core-memory-overview.jpg)

---

## Why

Most "agent memory" is either:
- raw chat logs (bloats context, low signal), or
- vector similarity recall (non-deterministic, hard to debug)

Core Memory is different:
- explicit causal structure (graph of beads + links)
- lossless storage + lossy injection (compaction tiers)
- debuggable and reproducible (store-backed, deterministic assembly)

---

## Concepts

### Beads
A bead is a small, structured memory unit:
- type: `decision`, `lesson`, `outcome`, `goal`, `evidence`, ...
- title + summary bullets
- tags/scope/session metadata
- lifecycle state (`open` / `promoted` / `compacted` / `superseded` / `tombstoned`)

### Associations
Associations connect beads explicitly with a named relationship (e.g. `derives-from`, `supersedes`, `validates`).

#### Authority
- **authored**: created explicitly by agent/user (canonical truth, immutable)
- **derived**: inferred by analysis/crawlers (optional, pruneable via myelination)

### Sessions
Beads are grouped into sessions. Core Memory maintains a session index to support rolling-window selection and compaction.

### Compaction tiers
Compaction is render-layer only (store remains lossless):
- full (new/recent)
- summary (older)
- minimal (anchors)
- tombstoned (not injected; still traversable for audit)

### Context Packet
Each turn, Core Memory produces a Context Packet: an ordered, token-budgeted set of compacted bead renders drawn from the last N sessions and relevant association chains.

`promoted-context.md` is an optional debug artifact for human inspection. It is regenerated from current store state and is not canonical memory state.

---

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Configure store root:

```bash
export CORE_MEMORY_ROOT="$PWD/memory"
```

---

## Quickstart

Create a bead:

```bash
core-memory --root "$CORE_MEMORY_ROOT" add --type decision --title "Use stdlib only" --session-id main --tags core-memory
```

Query beads:

```bash
core-memory --root "$CORE_MEMORY_ROOT" query --type decision --limit 5
```

Compaction + restore:

```bash
core-memory --root "$CORE_MEMORY_ROOT" compact --session main --promote
core-memory --root "$CORE_MEMORY_ROOT" uncompact --id <bead_id>
```

Migrate legacy store:

```bash
core-memory --root "$CORE_MEMORY_ROOT" migrate-store --legacy-root /path/to/legacy/.mem-beads
```

---

## Environment

- Primary: `CORE_MEMORY_ROOT` (recommended)
- Compatibility accepted by migration tooling: `MEMBEADS_ROOT`, `MEMBEADS_DIR`
- CLI default root when unset: `./memory`

## Platform note

Current file-locking uses POSIX `fcntl`, so write-lock behavior is POSIX-first (Linux/macOS/WSL).
For native Windows support, a lock fallback implementation is still needed.

## Store layout

```text
<root>/
  .beads/
    index.json
    global.jsonl
    session-<id>.jsonl
    archive.jsonl
    .lock
    events/
      global.jsonl
      session-<id>.jsonl
  .turns/
    session-<id>.jsonl
```

---

## CLI reference (most-used)

- `core-memory add ...` — create bead
- `core-memory query ...` — inspect memory state
- `core-memory compact ...` — compact bead detail (lossless via archive)
- `core-memory uncompact ...` — restore compacted detail
- `core-memory myelinate ...` — deterministic myelination pass output
- `core-memory migrate-store ...` — import legacy mem_beads stores

---

## How context injection works

1. Read beads + authored associations from store
2. Select relevant sessions/chains under token budget
3. Apply compaction tiers (render-only)
4. Emit deterministic Context Packet

Given the same store + config, packet assembly is deterministic.

---

## Myelination (optional)

Myelination operates on **derived** associations only.
It can reinforce frequently useful derived associations and prune weak/noisy ones without mutating authored causal truth.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for design invariants, terminology, and development setup.

Run tests:

```bash
pytest -q
```

---

## Roadmap

- Graph DB backend (optional)
- Myelination policy expansion (derived associations)
- Session digest bead
- Better token estimation based on render formats
- Pluggable retrieval strategies
- Windows lock fallback (cross-platform)

---

## Compatibility note

- `core-memory` is canonical.
- Legacy `mem_beads` runtime code and `mem-beads` command alias have been removed.
- Use `core-memory migrate-store` to import older `.mem-beads` stores.

## License

MIT
