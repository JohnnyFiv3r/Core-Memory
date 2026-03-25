# Good First Issue Seed Set

Create exactly 6 issues (2 docs, 2 testing, 2 examples). Each should include title, short description, acceptance criteria, and `good first issue` label.

## Docs (2)

### 1) Docs: Clarify doctor output fields in CLI reference
**Description:** Add a concise section in CLI docs explaining each `core-memory doctor` check and why it can fail locally. Keep it contributor-focused and copy/paste friendly.
**Acceptance criteria:**
- CLI docs include all doctor check names.
- Each check has one-line interpretation.
- Includes one remediation example for a failed check.

### 2) Docs: Add contributor quick path in docs index
**Description:** Add a “Contributor first 10 minutes” subsection in `docs/index.md` linking quickstart, CONTRIBUTING, and smoke script.
**Acceptance criteria:**
- New subsection exists near top of docs index.
- Links resolve to current files.
- No archive/history docs are listed in this subsection.

## Testing (2)

### 3) Testing: Add doctor command contract tests
**Description:** Add tests for `core-memory doctor` PASS/FAIL behavior on valid and intentionally broken local stores.
**Acceptance criteria:**
- Covers success case and at least 2 failure cases.
- Asserts exit code semantics (0 pass, 1 fail).
- Asserts PASS/FAIL line output includes check names.

### 4) Testing: Add smoke script CI invocation test
**Description:** Add a lightweight CI or test harness check that `scripts/run_contributor_smoke.sh` is executable and returns 0 in a clean environment.
**Acceptance criteria:**
- Script is exercised in automation.
- Failure output is visible in logs.
- Test does not require external network services beyond package install.

## Examples (2)

### 5) Examples: Add retrieval example using memory.execute
**Description:** Add `examples/memory_execute_basic.py` showing request payload + printed result with citations.
**Acceptance criteria:**
- Uses canonical public tool surface.
- Runs with `PYTHONPATH=. python3 ...`.
- Includes comment block with expected output shape.

### 6) Examples: Add bridge-local dry run example
**Description:** Add an example script that writes a synthetic finalized-turn payload to bridge integration entrypoint without requiring OpenClaw runtime.
**Acceptance criteria:**
- No live OpenClaw daemon required.
- Produces deterministic JSON result.
- Includes clear cleanup instructions for generated local files.
