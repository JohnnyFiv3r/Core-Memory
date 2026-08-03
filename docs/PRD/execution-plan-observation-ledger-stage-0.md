# Stage 0 Execution Plan: Contract, Evaluation, and Legacy Inventory

- **Status:** Approved for sequential implementation
- **Parent plan:** `execution-plan-observation-ledger-architecture.md`
- **Governing PRD:** `observation-ledger-architecture.md`
- **Architecture contract:** `../contracts/observation-ledger-architecture.md`
- **Phases:** `PR-00A` through `PR-00T`
- **Delivery rule:** one phase, one complete push, one independently mergeable PR

## 1. Outcome

Stage 0 creates the enforceable architecture contract, trustworthy evaluation
system, approved quality gates, and complete legacy-state inventory required
before the Observation Ledger migration begins.

Stage 0 does not migrate production data, replace runtime subsystems, create a
dual writer, or assert that the legacy engine already meets the eventual
quality thresholds.

The phase dependency is strict:

```text
00A -> 00B -> 00C -> 00D -> 00E -> ... -> 00S -> 00T
```

Each phase begins from main only after its predecessor merges. Review fixes
remain inside the originating PR. A phase cannot merge as scaffolding whose
truth depends on a follow-up.

## 2. Non-negotiable contracts

- A bead documents an observed user-agent event.
- LLM-authored labels and prose are constrained to supplied event evidence.
- Assertions are optional; an empty assertion set is a valid result.
- Deterministic code owns mechanics, never semantic authorship.
- Provider failure or invalid semantic output writes no fallback meaning.
- Claims resolve through explicit revision chains.
- Unresolved incompatible claims return ambiguity.
- Promotion affects context expansion only and cannot upgrade truth.
- Benchmark contamination disqualifies a run.
- The existing architecture guard and `benchmarks/` package remain canonical.
- Stage 0 changes no public product API.

## 3. Atomic phase register

| Phase | Complete PR outcome | Merge proof |
|---|---|---|
| `PR-00A` | Architecture contract, guard v2, expiring exceptions, parent register, and self-tests | Required guard rejects new/increased authority and fallback debt. |
| `PR-00B` | Observation Ledger benchmark framework and CLI | Schemas, explicit model selectors, run states, contamination handling, reports, and test adapters work end to end. |
| `PR-00C` | Static code and filesystem inventory | Read-only scanner classifies authorities, readers, writers, stores, fallbacks, and queues. |
| `PR-00D` | Database and consolidated inventory | SQLite, PostgreSQL, and known hosted authorities reconcile without mutation or unknown classifications. |
| `PR-00E` | 25 statement/request observation cases | Inputs, gold, accepted adjudications, evidence, checksums, and tests are complete. |
| `PR-00F` | 25 goal/decision observation cases | Same complete corpus contract. |
| `PR-00G` | 25 action/result observation cases | Same complete corpus contract. |
| `PR-00H` | 25 evidence/reflection observation cases | Observation corpus reaches 100 broad cases. |
| `PR-00I` | 25 low-semantic/noise/adversarial cases | Thin beads and zero-assertion outcomes are represented. |
| `PR-00J` | 25 core claim/revision cases | Extraction, reaffirmation, supersession, retraction, abstention, and as-of behavior are covered. |
| `PR-00K` | 25 adversarial/temporal claim cases | Contest, ambiguity, broken chains, cycles, event time, and knowledge time are covered. |
| `PR-00L` | 25 noncausal association cases | Support, contradiction, part-of, similarity, refinement, and no-link are covered. |
| `PR-00M` | 25 causal/directional/temporal association cases | Cause, enable, block, dependency, resolution, supersession, and time are covered. |
| `PR-00N` | 25 adversarial association cases | Direction traps, chronology, source correlation/independence, contradiction, and no-link are covered. |
| `PR-00O` | 30 artifact cases | Ten artifact kinds plus evidence, revision, conflict, and abstention are covered. |
| `PR-00P` | 30 promotion/context cases | Token pressure, pins, compression, critical retention, downstream tasks, and unpack are covered. |
| `PR-00Q` | 25 semantic/current-state retrieval cases | Semantic, current-truth, and bi-temporal retrieval are covered. |
| `PR-00R` | 25 causal/association/hydration retrieval cases | Causal paths, source hydration, archive unpacking, and evidence are covered. |
| `PR-00S` | 25 adversarial retrieval cases | Ambiguity, abstention, citations, injection, unavailable sources, and degradation are covered. |
| `PR-00T` | Public/private live baselines and final gate | User-selected models, uncontaminated reports, approved vocabulary, thresholds, and sanitized private aggregate are complete. |

## 4. PR-00A implementation contract

PR-00A upgrades `scripts/check_architecture_guards.py` and its existing baseline
in place. It does not create a parallel architecture checker.

The v2 exception registry requires a stable ID, invariant IDs, category, exact
path, symbol, fingerprint, allowed and forbidden behavior, justification,
provenance, owner, deletion PR, and occurrence ceiling.

The guard must:

- reject wildcard and absolute exception paths;
- reject missing owners, provenance, or expiry phases;
- reject stale and duplicate fingerprints;
- allow debt occurrences to decrease;
- reject occurrence increases and unregistered semantic fallbacks;
- activate target-package import boundaries when target packages appear;
- exclude comments, documentation, fixtures, and historical readers from
  runtime semantic-authoring judgments where appropriate;
- produce a read-only report;
- emit baseline candidates only to a separate review file;
- refuse to overwrite the canonical baseline through its CLI.

PR-00A also adds this Stage 0 plan, the governing PRD and parent execution plan,
the architecture contract, PRD index entries, required workflow coverage, and
focused regression tests. It changes no runtime behavior.

## 5. Benchmark interface for PR-00B

The implementation extends `benchmarks/contracts.py` and adds
`benchmarks/observation_ledger/`.

Required commands:

```text
python -m benchmarks.observation_ledger validate \
  --pack <path> --visibility public|private

python -m benchmarks.observation_ledger adjudicate \
  --pack <path> --judge-model <provider:model> --out <path>

python -m benchmarks.observation_ledger run \
  --pack <path> \
  --author-model <provider:model> \
  --judge-model <provider:model> \
  --out <path>

python -m benchmarks.observation_ledger baseline \
  --public-report <path> \
  --private-report <path> \
  --thresholds <path> \
  --out <path>
```

Both runtime selections are made explicitly by the user. There are no default
models, frontier requirements, provider-diversity requirements, or automatic
substitutions. Author and judge may be the same model.

The author selection supplies every product semantic role exercised by the
run. The judge selection is benchmark-only and cannot write engine state.
Reports classify the selections as `same_model`, `same_provider`, or
`cross_provider`; classification is informational.

Missing selectors, credentials, or provider support produce `blocked`, never a
fallback. Harness contamination produces `disqualified`. Infrastructure
failure produces `failed`. An honest run that exercises a product semantic
fallback is `completed` with a hard-invariant failure.

Required run states are:

- `completed`
- `blocked`
- `disqualified`
- `failed`

The report schema is `observation_ledger.report.v1`. It records commit, pack
visibility/checksum, runtime configuration, requested/resolved models,
prompts/schemas, selection classification, contamination flags, state, counts,
hard invariants, quality metrics, latency, tokens, cost, and limitations.
Per-case output is allowed only for the public pack.

## 6. Corpus and adjudication contract

The public corpus contains exactly 385 minimum cases:

- 125 observations;
- 50 claim/revision cases;
- 75 association cases;
- 30 artifact cases;
- 30 promotion cases;
- 75 retrieval cases.

Inputs, gold, and adjudications are physically separated. Runtime inputs cannot
contain gold or judge fields. Every public case includes a privacy declaration.

Gold uses atomic propositions and evidence spans rather than exact prose. A
selected judge model critiques proposed gold. A human then accepts or revises
it. The checksum-bound human decision is final; no second human or independent
frontier model is required.

Every corpus PR contains the input, gold, accepted adjudication, manifest,
checksums, and focused tests for all cases in that phase.

## 7. Private-pack contract

The private pack uses the public schemas but lives at an explicit absolute path
outside the repository. It has no default path. Validation rejects a private
pack stored inside the repository.

The pack contains at least 60 representative real-interaction cases, including
at least 10 observations, claims, associations, artifacts, promotion cases,
and retrieval cases. Every release-gating case has accepted human adjudication.

Raw events, excerpts, case IDs, gold, adjudications, and per-case results are
never committed or uploaded. The committed aggregate contains only checksum,
bucket counts, aggregate metrics, model/prompt/schema versions, selection
classification, latency/tokens/cost, invariant outcomes, and sanitized
limitations.

## 8. Quality gate

Stage 0 uses a dual gate:

1. Zero hard-invariant violations.
2. User-approved absolute semantic floors and no statistically meaningful
   regression from the accepted baseline.

Hard invariants include no contamination, evidence-valid semantics and
citations, tenant isolation, no deterministic semantic output, fail-closed
provider behavior, no implicit latest-wins resolution, and no truth upgrade
from salience, repetition, frequency, or promotion.

Binary metrics use 95 percent Wilson intervals. Continuous and task metrics use
seeded bootstrap 95 percent confidence intervals. The threshold manifest stores
the allowed regression per metric. Baselines never update automatically.

The implementer may report measurements but cannot choose or lower release
floors. Vocabulary, floors, and regression allowances require explicit user
approval in PR-00T. Stage 0 may complete even when legacy master fails those
future floors, provided the measurement is honest and the gates are approved.

## 9. Inventory interface for PR-00C and PR-00D

The read-only inventory commands are:

```text
scan-code
scan-filesystem
scan-sql
merge
verify
```

Every root, DSN, source label, and output path is explicit. Scanners refuse `/`,
a home directory, or a workspace root as a broad data target. SQL runs through
a read-only role or transaction. Credentials and DSNs are redacted.

Inventory records include source label, tenant/workspace, path or table kind,
authority class, counts, bytes, event-time range, stable hash, parse failures,
duplicate IDs, recoverability, provenance, readers, writers, future importer,
and deletion PR.

Provenance values are:

- `observed_source`
- `llm_authored_with_receipt`
- `llm_authored_without_receipt`
- `deterministic_derived`
- `human_authored`
- `projection_only`
- `unknown`

PR-00D cannot merge with an inaccessible or unclassified known local, hosted,
or production authority. Inventory never imports, repairs, mutates, or deletes
source state.

## 10. Stage completion

Stage 0 is complete only when:

- PR-00A through PR-00T merge sequentially;
- guard v2 is required CI and architecture debt cannot increase;
- 385 public cases validate and have accepted adjudication;
- the private pack meets its 60-case and per-domain minimums;
- public and private live baselines complete with explicit user selections;
- neither run is contaminated;
- only the sanitized private aggregate is committed;
- vocabulary and quality gates are explicitly approved;
- repository, filesystem, local database, and hosted authority inventories
  reconcile;
- no runtime migration or authority switch has occurred.

Stage 1 semantic contracts depend on PR-00A and PR-00E through PR-00N. Stage 1
schema work depends on PR-00D. Stage 2 verification depends on PR-00T. Stage 10
import work depends on PR-00D. No semantic implementation stage may bypass the
PR-00T review checkpoint.
