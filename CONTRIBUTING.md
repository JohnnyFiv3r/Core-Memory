# Contributing to Core Memory

Thanks for helping improve Core Memory.

## Quick start

```bash
git clone https://github.com/JohnnyFiv3r/Core-Memory.git
cd Core-Memory
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
# optional test/lint extras
pip install -e '.[dev]'
```

Run a minimal sanity check:

```bash
PYTHONPATH=. python3 examples/quickstart.py
```

## Running tests

```bash
.venv/bin/pytest -q
```

You can also run focused suites, for example:

```bash
.venv/bin/pytest -q tests/test_memory_execute_contract.py
.venv/bin/pytest -q tests/test_openclaw_integration.py
```

## Lint / type checks (if installed)

```bash
.venv/bin/ruff check core_memory/
.venv/bin/mypy core_memory/
```

## Good first issue scope

Great starter contributions:
- docs clarity fixes in `docs/`
- adding focused tests for existing behavior
- small retrieval explainability improvements (debug fields)
- tightening error messages / diagnostics

Please avoid broad refactors in a first PR.

## PR process

1. Open an issue (or comment on existing issue) with proposed scope.
2. Keep PRs small and atomic.
3. Include tests or a clear validation note.
4. Update docs when behavior/surface changes.

## Design invariants (do not break)

1. **Archive-first durability**: session/global JSONL is the write authority.
2. **Index is projection cache**: `index.json` must be rebuildable.
3. **Deterministic retrieval contracts**: avoid nondeterministic output shape changes.
4. **Lock-protected writes**: no unsafe concurrent writes to `.beads/`/`.turns/`.

## Contributor etiquette

- Be explicit about tradeoffs.
- Prefer clarity over cleverness.
- If introducing new public surface, document it in `docs/public_surface.md`.
