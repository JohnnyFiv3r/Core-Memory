# Contributing to Core Memory

## Quick start

```bash
git clone https://github.com/JohnnyFiv3r/Core-Memory.git
cd Core-Memory
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## Running tests

```bash
python3 test_phase1_parity.py
PYTHONPATH=. python3 test_edges.py
PYTHONPATH=. python3 test_e2e.py
```

Or with pytest:

```bash
.venv/bin/pytest -q
```

## Lint and type check

```bash
.venv/bin/ruff check core_memory/
.venv/bin/mypy core_memory/
```

## Design invariants (do not break)

1. **Lossless storage.** Compaction is render-layer only; the archive retains full detail forever.
2. **Authored associations are immutable truth.** Only derived associations may be pruned.
3. **Deterministic assembly.** Same store + config must produce the same Context Packet.
4. **Archive-first durability.** JSONL archive is written before the index for crash safety.
5. **All writes under store lock.** No write to `.beads/` or `.turns/` outside `store_lock()`.

## Terminology

Core Memory uses precise terms for different concepts:

| Term | Meaning | Where |
|---|---|---|
| **bead** | Atomic memory unit (decision, lesson, outcome, etc.) | `index.json` beads, session JSONL |
| **association** | Explicit link between two beads | `index.json` associations list |
| **relationship** | The type/label of an association (`follows`, `supersedes`, `derives-from`, etc.) | `association.relationship` field |
| **authored** | Created explicitly by agent or user (canonical truth) | default for all associations |
| **derived** | Inferred by crawlers/analysis (optional, pruneable) | future: myelination targets |
| **recall** | Recording that a bead was accessed/used (strengthens it) | `bead.recall_count` |
| **compaction** | Archiving bead detail while keeping summary in index | `compact` / `uncompact` |

Avoid using "edge" or "link" in new code — prefer "association" consistently.

## File layout

```
core_memory/
  __init__.py       Package init
  cli.py            CLI entry point
  store.py          MemoryStore (all persistence)
  events.py         Event log + rebuild
  io_utils.py       Atomic writes + store lock
  models.py         Enums (BeadType, Status, Scope, Authority)
  dreamer.py        Association analysis (optional)
  adapter_cli.py    Legacy command translation (compat)
  py.typed          PEP 561 marker
```

## Pull request checklist

- [ ] All tests pass
- [ ] No writes outside `store_lock()`
- [ ] No direct `open(..., 'w')` on index — use `atomic_write_json()`
- [ ] No direct `open(..., 'a')` on JSONL — use `append_jsonl()`
- [ ] Terminology consistent (bead, association, relationship)
