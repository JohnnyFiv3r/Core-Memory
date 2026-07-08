# PRD: Agentic Semantic Task Runtime
## PydanticAI Operator Harness, Model Routing, and Sub-Agent Delegation

**Status:** Draft v1
**Owner surface:** Core Memory semantic runtime; hosted operator experience
**Related:** `03a-pydanticai-boundary.md`, `dreamer-continuity-engine.md`,
`myelination-reinforcement.md`, `soul-files.md`, `external-data-bead-ingest.md`

---

## 1. Summary

Core Memory currently invokes LLMs in several semantic roles: bead-field judging,
rationale extraction, association judging, Dreamer research, SOUL proposal work,
and future document/source enrichment. These invocations are useful but
architecturally scattered. They use different prompts, model-selection rules,
fallback paths, provenance records, retry semantics, and observability shapes.

This PRD proposes a unified **Agentic Semantic Task Runtime**: every LLM-backed
semantic operation is expressed as a typed task, routed through a single
operator harness, delegated to the appropriate sub-agent/model tier, validated
against structured output contracts, and returned to Core Memory through
canonical governed write or review surfaces.

The preferred hosted implementation is a **PydanticAI operator harness**. It acts
as Core Memory's semantic operator: cheap sub-agents handle high-volume judging
and classification; stronger models handle uncertain association decisions; a
frontier model handles Dreamer research and SOUL-adjacent synthesis. A hosted
operator UI then becomes the operator seat for this agent: the agent performs
semantic work, Core Memory audits and governs it, and the user leads, reviews,
redirects, and grants authority.

Core Memory remains the deterministic memory kernel. The operator harness does
not directly mutate memory, rewrite truth, or bypass governance. It produces
structured task results that Core Memory validates and applies only through
existing surfaces such as `capture`, typed external-evidence ingest, association
candidate decisions, Dreamer candidate review, SOUL proposals, and `maintain`.

---

## 2. Product Thesis

A hosted memory application should not feel like a passive database with
background scripts. It should feel like a working memory operator. The user is
the leader and partner; the agent is the semantic operator that performs memory
labor:

- judges what a turn means,
- writes richly typed bead proposals,
- detects candidate associations,
- researches longer-horizon continuity,
- explains why memories connect,
- asks for review when authority is needed,
- maintains the graph without hiding the audit trail.

The architectural move is to put a first-party agent at the center of every
semantic operation while keeping Core Memory as the trusted substrate. The agent
can be creative and adaptive; Core Memory stays strict, replayable, and
governed.

---

## 3. Goals

1. Route every LLM invocation in Core Memory through a common semantic task
   envelope.
2. Introduce a core operator abstraction that can be implemented by PydanticAI
   without making `pydantic-ai` a hard dependency of `import core_memory`.
3. Support sub-agent delegation by task type and risk level:
   - cheap bead judge/classifier,
   - cheap or mid-tier association candidate generator,
   - mid-tier or frontier association judge,
   - frontier Dreamer researcher,
   - verifier/schema auditor when needed.
4. Standardize model routing, prompt versions, rubric versions, grounding
   inputs, output schemas, fallback modes, and provenance.
5. Preserve all Core Memory authority boundaries: model output is never truth by
   itself.
6. Make hosted operator surfaces able to show operator work as first-class
   activity: task started, sub-agent chosen, evidence read, candidates produced,
   decisions awaiting review, writes applied.
7. Improve cost/performance by making cheap tasks cheap and expensive tasks
   deliberately expensive.
8. Make semantic work inspectable, replayable, benchmarkable, and tunable.

---

## 4. Non-Goals

1. Do not make `pydantic-ai` a required dependency for Core Memory core imports.
2. Do not let an LLM write directly to `.beads`, graph projections, SOUL files,
   or event logs outside canonical runtime APIs.
3. Do not collapse user authority into model authority.
4. Do not replace deterministic fallbacks where they are needed for degraded
   operation.
5. Do not require hosted operator surfaces to expose every low-level task to the
   user.
6. Do not create a general-purpose autonomous agent loop that can perform
   arbitrary tool calls against memory.
7. Do not merge Dreamer findings, SOUL statements, and association edges into a
   single undifferentiated "agent thought" layer.

---

## 5. Design Principles

### 5.1 Core Memory is the kernel

Core Memory owns:

- schemas,
- validation,
- persistence,
- append-only audit,
- idempotency,
- replay,
- authority checks,
- source provenance,
- graph application,
- lifecycle state transitions.

The agent runtime owns:

- task planning,
- prompt selection,
- model routing,
- sub-agent delegation,
- structured output production,
- retry strategy,
- confidence estimation,
- rationale generation.

### 5.2 Model output is a proposal until governed

Model output can be:

- a candidate bead,
- a judged field bundle,
- an association candidate,
- an association decision,
- a Dreamer finding,
- a SOUL revision proposal,
- a verifier report.

It is not canonical truth unless it passes the appropriate Core Memory write or
review path.

### 5.3 Cheap work should use cheap agents

Most semantic work is high-volume and should be inexpensive:

- bead type classification,
- entity/claim extraction,
- section summarization,
- candidate edge generation,
- obvious association rejection.

Dreamer and SOUL-adjacent tasks are different: they require synthesis across
large context windows and should use frontier models deliberately.

### 5.4 Every semantic task must be auditable

Every task result must carry:

- task type,
- task id,
- input hash,
- evidence refs,
- source envelope refs,
- model profile,
- model name where available,
- prompt version,
- rubric/schema version,
- fallback mode,
- output schema version,
- latency/cost metadata where available,
- authority boundary applied.

### 5.5 PydanticAI is an implementation, not the core contract

The core contract is a provider-neutral semantic task interface. The primary
hosted implementation is PydanticAI. The optional dependency boundary remains:
core imports must not side-load `pydantic_ai`.

---

## 6. System Architecture

```text
Core Memory runtime event
  -> SemanticTaskRequest
  -> SemanticTaskRuntime
  -> Operator harness
  -> Task-specific sub-agent
  -> Model tier selection
  -> Structured task result
  -> Core Memory validator/governance
  -> Canonical write, candidate queue, or review action
```

### 6.1 Core components

#### SemanticTaskRequest

The typed envelope for any semantic task.

```python
class SemanticTaskRequest(TypedDict):
    task_id: str
    task_type: str
    task_version: str
    priority: Literal["inline", "interactive", "background", "batch"]
    risk_level: Literal["low", "medium", "high", "critical"]
    cost_tier: Literal["cheap", "standard", "frontier", "auto"]

    root: str
    tenant_id: str | None
    session_id: str | None
    source_event_id: str | None
    source_ingest_envelope_refs: list[dict]

    evidence_refs: list[dict]
    bead_ids: list[str]
    candidate_ids: list[str]
    context: dict

    prompt_version: str
    rubric_version: str
    output_schema: str
    idempotency_key: str
```

#### SemanticTaskResult

The typed result returned by the operator harness.

```python
class SemanticTaskResult(TypedDict):
    ok: bool
    task_id: str
    task_type: str
    status: Literal[
        "completed",
        "no_op",
        "needs_review",
        "failed",
        "fallback_used",
        "quarantined",
    ]

    output: dict
    validation_errors: list[dict]
    warnings: list[dict]

    model_profile: dict
    prompt_version: str
    rubric_version: str
    output_schema: str
    grounding_hash: str
    input_hash: str
    output_hash: str

    latency_ms: int | None
    token_usage: dict
    cost_estimate: dict

    fallback_mode: str | None
    authority_boundary: str
    source_ingest_envelope_refs: list[dict]
    evidence_refs: list[dict]
```

#### SemanticTaskRuntime

Provider-neutral interface used by Core Memory.

```python
class SemanticTaskRuntime(Protocol):
    def run_task(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        ...
```

Implementations:

- `HeuristicSemanticTaskRuntime` — deterministic fallback.
- `ProviderNeutralLLMTaskRuntime` — current lightweight provider-neutral bridge.
- `PydanticAISemanticTaskRuntime` — preferred hosted/operator implementation.

### 6.2 PydanticAI operator harness

The PydanticAI implementation owns sub-agent routing. It should be available
through an integration module such as:

```text
core_memory.integrations.pydanticai.semantic_tasks
```

The core runtime imports the interface, not the implementation. Hosted
deployments can configure:

```text
CORE_MEMORY_SEMANTIC_TASK_RUNTIME=pydanticai
CORE_MEMORY_AGENT_MODEL_CHEAP=...
CORE_MEMORY_AGENT_MODEL_STANDARD=...
CORE_MEMORY_AGENT_MODEL_FRONTIER=...
CORE_MEMORY_AGENT_MODEL_VERIFIER=...
```

`CORE_MEMORY_SEMANTIC_RUNTIME` remains accepted as a legacy alias, but
`CORE_MEMORY_SEMANTIC_TASK_RUNTIME` is the canonical deployment flag.

If unavailable, Core Memory falls back to provider-neutral or heuristic behavior
according to task policy.

---

## 7. Task Families

### 7.1 Bead field judge

**Purpose:** Author semantic bead fields from finalized turns, transcript
snapshots, document sections, or source events.

Inputs:

- user/assistant turn content,
- source metadata,
- existing session context,
- source envelope refs.

Outputs:

- bead type,
- title,
- summary,
- entities,
- claims,
- rationale,
- suggested tags,
- confidence,
- retrieval wording,
- source refs.

Default model tier: cheap.

Fallback: deterministic heuristic judge.

Authority boundary: output enriches bead creation request, but Core Memory
normalizes schema and writes through canonical turn/external ingest paths.

### 7.2 Claim/entity extractor

**Purpose:** Extract structured claims and entities as reusable semantic atoms.

Inputs:

- candidate bead content,
- document section text,
- prior entity registry hints,
- source evidence refs.

Outputs:

- claim candidates,
- claim kind,
- confidence,
- entities,
- alias hints,
- provenance.

Default model tier: cheap.

Fallback: existing heuristic claim/entity extraction.

Authority boundary: claims are normalized and supersession/update policy remains
Core Memory-owned.

### 7.3 Association candidate generator

**Purpose:** Generate possible edges before a judge decision.

Inputs:

- source bead,
- candidate bead set,
- graph neighborhood,
- source envelope refs,
- retrieval hints,
- myelination/retrieval-value signals.

Outputs:

- candidate edge rows,
- proposed relationship,
- confidence prior,
- evidence refs,
- rationale,
- reason code.

Default model tier: cheap or standard.

Fallback: existing candidate proposal heuristics.

Authority boundary: candidates are not active graph truth.

### 7.4 Association decision judge

**Purpose:** Decide whether association candidates should become active graph
edges.

Inputs:

- association candidate context,
- source/target beads,
- evidence refs,
- graph constraints,
- relationship taxonomy,
- rubric.

Outputs:

- accept/reject/modify/invert/no_link,
- relationship,
- reason text,
- evidence refs,
- confidence,
- reviewed bead states.

Default model tier: standard.

Escalation: frontier when:

- high-risk bead types,
- contradictory evidence,
- low confidence,
- high graph centrality,
- SOUL/goal implications,
- source authority mismatch.

Fallback: terminal `pending_judge` or `no_supported_links` depending on policy;
do not retry indefinitely.

Authority boundary: active graph edges are written only through association
decision/application paths.

### 7.5 Dreamer researcher

**Purpose:** Run long-horizon continuity research over storylines, SOUL files,
goals, Dreamer history, association graph, myelination/retrieval signals, and
source evidence.

Inputs:

- storyline projection,
- graph summary,
- SOUL files,
- goals,
- prior Dreamer candidates/decisions,
- myelination manifest,
- retrieval-feedback summaries,
- target research question.

Outputs:

- Dreamer candidates,
- findings,
- future vectors,
- tension candidates,
- identity/value candidates,
- storyline projection candidates,
- confidence and falsifiability notes,
- evidence refs.

Default model tier: frontier.

Fallback: deterministic Dreamer detectors where available; otherwise no-op with
diagnostics.

Authority boundary: Dreamer emits candidates/findings only. It does not modify
grounded memory, create Goal Beads, or write SOUL files directly.

### 7.6 SOUL proposal assistant

**Purpose:** Draft SOUL revisions from accepted Dreamer findings, user-approved
goals, integrity checks, or explicit user requests.

Inputs:

- current SOUL files,
- accepted Dreamer candidates,
- goal lifecycle state,
- evidence refs,
- user instruction.

Outputs:

- SOUL revision proposal,
- target file,
- entry key,
- operation,
- rationale,
- citations,
- risk label.

Default model tier: frontier plus verifier.

Authority boundary: SOUL changes require the existing proposal/approval flow.

### 7.7 Verifier/auditor

**Purpose:** Check outputs before Core Memory accepts them into candidate queues
or governed apply paths.

Inputs:

- task output,
- output schema,
- evidence refs,
- policy rubric.

Outputs:

- schema validity,
- unsupported inference warnings,
- missing provenance,
- authority mismatch,
- recommended quarantine/no-op.

Default model tier: cheap or standard; deterministic validation always runs
first.

Authority boundary: verifier cannot approve truth; it can only block, warn, or
route to review.

---

## 8. Model Routing Policy

### 8.1 Default tiers

| Task | Default tier | Escalation |
|---|---|---|
| Bead field judge | cheap | standard on malformed/low-confidence output |
| Claim/entity extraction | cheap | standard on conflicting entity identity |
| Association candidate generation | cheap | standard for sparse/high-value contexts |
| Association decision | standard | frontier for high-risk/ambiguous edges |
| Dreamer research | frontier | none; reduce scope before downgrade |
| SOUL proposal | frontier | verifier pass required |
| Verifier | cheap/standard | frontier only for high-risk self-model conflicts |

### 8.2 Routing inputs

Routing should consider:

- task type,
- risk level,
- user/workspace policy,
- evidence volume,
- graph centrality,
- source authority,
- expected cost,
- latency target,
- prior failure rate for that task profile,
- requested mode (`inline`, `interactive`, `background`, `batch`).

### 8.3 Example configuration

```yaml
semantic_runtime:
  provider: pydanticai
  default_timeout_ms: 30000
  max_retries: 2

  models:
    cheap:
      provider: openai
      model: gpt-4.1-mini
    standard:
      provider: anthropic
      model: claude-haiku-4-5
    frontier:
      provider: anthropic
      model: claude-sonnet-4-5
    verifier:
      provider: openai
      model: gpt-4.1-mini

  task_profiles:
    bead_field_judge:
      tier: cheap
      fallback: heuristic
    association_decision:
      tier: standard
      escalate_on_low_confidence: true
    dreamer_research:
      tier: frontier
      fallback: no_op
```

---

## 9. Authority And Governance

### 9.1 Authority classes

Task output can carry one of these authority classes:

- `model_suggestion`
- `agent_judged`
- `operator_reviewed`
- `user_confirmed`
- `admin_repair`
- `system_of_record_event`

Only some classes can write to active truth. For example:

- Bead field judge output can enrich a bead write but must be normalized.
- Association candidate generation creates candidate rows only.
- Association decisions may write edges only when the decision path has the
  required authority.
- Dreamer creates findings/candidates only.
- SOUL proposals require approval.

### 9.2 Maintain as the operator control plane

The `maintain()` facade should become the primary control surface for a hosted
operator agent:

- inspect coverage,
- run association sweeps,
- list candidates,
- decide reviewed candidates,
- refresh myelination,
- run Dreamer jobs,
- inspect/apply SOUL proposals,
- remove mistaken beads or source-derived memory with authority.

The operator harness may request `maintain()` actions, but Core Memory checks
authority and validates every mutation.

---

## 10. Observability

### 10.1 Semantic task log

Add a task log under events:

```text
.beads/events/semantic-task-runs.jsonl
```

Each row should include:

- task id,
- task type,
- status,
- model tier,
- provider/model,
- prompt/rubric/schema version,
- input/output hashes,
- evidence refs,
- source envelope refs,
- duration,
- token usage/cost estimate,
- fallback mode,
- error category,
- resulting candidate/write ids.

### 10.2 Hosted activity surface

Hosted operator surfaces should be able to render:

- "Judged 18 document sections"
- "Generated 42 association candidates"
- "Accepted 13 low-risk edges"
- "Queued 5 candidates for review"
- "Dreamer produced 3 findings"
- "SOUL proposal awaiting approval"

The UI should not display raw sub-agent chatter by default. It should display
compact operator activity with drill-down into evidence and task receipts.

### 10.3 Metrics

Track:

- task count by type,
- success/fallback/quarantine rates,
- model cost by task type,
- average latency,
- schema validation failure rate,
- human acceptance/rejection rate,
- association precision after review,
- Dreamer candidate acceptance rate,
- retry count,
- source coverage impact.

---

## 11. Failure Modes

### 11.1 Model unavailable

Behavior:

- cheap tasks can use deterministic fallback,
- association decisions can remain `pending_judge`,
- Dreamer can no-op with diagnostics,
- SOUL proposal tasks should not fallback to weak unsupervised writes.

### 11.2 Invalid structured output

Behavior:

- retry with repair prompt if task policy allows,
- otherwise return validation errors,
- preserve failed task receipt,
- never partially apply malformed output.

### 11.3 Unsupported inference

Behavior:

- verifier flags unsupported claims,
- task may be quarantined,
- Dreamer findings must include falsifiability/evidence limitations.

### 11.4 Authority mismatch

Behavior:

- return `authority_required`,
- queue for review when appropriate,
- do not silently downgrade into an active write.

### 11.5 Cost runaway

Behavior:

- per-run budget,
- per-task max tokens/context,
- frontier model only on allowlisted task types,
- batch Dreamer with explicit run receipts.

---

## 12. Implementation Plan

### Phase 1 — Inventory and task envelope

1. Inventory all current LLM invocation points:
   - `policy/bead_judge.py`,
   - rationale/because extraction,
   - association judge path,
   - Dreamer candidate generation/research,
   - SOUL proposal/review helpers,
   - any document/source enrichment calls.
2. Add provider-neutral request/result schemas under
   `core_memory/schema/semantic_tasks.py`, with execution owned by
   `core_memory/policy/semantic_task_runtime.py` and run receipts owned by
   `core_memory/persistence/semantic_task_receipts.py`.
3. Add task id, task type, prompt version, rubric version, output schema, input
   hash, and source refs to existing LLM call receipts where feasible.
4. Preserve current behavior: existing direct calls may adapt into the envelope
   but should not change outputs yet.

Success criteria:

- All LLM-backed semantic paths can be named as task types.
- Tests prove task schemas are importable without `pydantic_ai`.
- No core import path loads `pydantic_ai`.

### Phase 2 — Runtime abstraction and fallbacks

1. Implement `SemanticTaskRuntime` protocol.
2. Implement deterministic/heuristic runtime wrappers for existing fallback
   paths.
3. Implement provider-neutral LLM runtime using existing `llm_client` where
   possible.
4. Add task-level fallback policy.
5. Add `semantic-task-runs.jsonl`.

Success criteria:

- Bead judge and association judge can run through the runtime abstraction.
- Existing test suite passes with no provider configured.
- Failed model calls produce task receipts, not silent fallback only.

### Phase 3 — PydanticAI operator implementation

1. Add optional PydanticAI implementation under integration boundary.
2. Define sub-agents:
   - bead judge agent,
   - association candidate agent,
   - association decision agent,
   - Dreamer researcher agent,
   - SOUL proposal agent,
   - verifier agent.
3. Add model profile configuration.
4. Add routing policy and escalation rules.
5. Add structured output schemas per task.

Success criteria:

- PydanticAI implementation runs only when optional extra/config is present.
- Cheap bead judge and frontier Dreamer can be configured separately.
- Tests prove `import core_memory` still does not side-load `pydantic_ai`.

### Phase 4 — Migrate semantic paths

Migrate in this order:

1. Bead field judge.
2. Rationale/because extraction.
3. Association candidate generation.
4. Association decision judge.
5. Dreamer researcher.
6. SOUL proposal assistant.

Rationale:

- High-volume cheap tasks prove cost controls first.
- Association decision proves authority boundaries.
- Dreamer/SOUL migration happens after receipts, routing, and review surfaces are
  stable.

### Phase 5 — hosted operator surface

1. Display semantic task activity in Settings/Explore activity surfaces.
2. Group activity by source, run, and task type.
3. Show "needs review" queues for association candidates, Dreamer findings, and
   SOUL proposals.
4. Let users approve/reject/redirect through `maintain()`.
5. Add workspace-level model routing policy later, behind safe defaults.

---

## 13. Testing Plan

### 13.1 Core tests

- Task schema validation.
- Runtime abstraction fallback behavior.
- No hard PydanticAI import from core modules.
- Bead judge parity tests before/after routing.
- Association decision parity tests before/after routing.
- Dreamer candidate generation snapshot tests.
- SOUL proposal authority tests.
- Task receipt persistence tests.

### 13.2 Integration tests

- Provider-neutral runtime with fake model client.
- PydanticAI runtime import guarded behind optional dependency.
- PydanticAI task execution with mocked agents.
- Model routing by task type and risk.
- Escalation from cheap to standard/frontier.

### 13.3 Governance tests

- Model output cannot directly write graph edges.
- Dreamer output cannot directly write SOUL files.
- SOUL proposals require approval.
- Association candidate decisions require authority.
- Failed/invalid outputs never partially apply.

### 13.4 Operational tests

- Task receipts include model profile and hashes.
- Cost/latency metrics are present when available.
- Fallback receipts are distinguishable from model receipts.
- Batch Dreamer can run without blocking ingest paths.

---

## 14. Acceptance Criteria

1. Every Core Memory LLM invocation has an associated semantic task type.
2. At least bead judge and association judge run through the semantic task
   runtime.
3. Dreamer can be configured to use a frontier model profile without changing
   bead judge model settings.
4. Core imports remain PydanticAI-optional.
5. Task receipts are written for successful, failed, and fallback semantic tasks.
6. Structured outputs are schema-validated before application.
7. Mutating semantic results flow through existing governed surfaces.
8. Hosted operator surfaces can inspect recent semantic task runs and show
   model/task status.
9. Test coverage proves no authority bypass and no hard optional dependency leak.

---

## 15. Open Questions

1. Should task receipts be append-only JSONL only, or also exposed through HTTP?
2. Should the semantic task runtime own retry policy globally, or should each
   task profile own retries?
3. Should Dreamer use one frontier researcher agent or a small team of
   specialized frontier sub-agents?
4. Should verifier be mandatory for all frontier outputs or only SOUL/Dreamer
   outputs?
5. Should model routing policy live in Core Memory config, hosted workspace
   config, or both?
6. How much of sub-agent reasoning should be persisted versus summarized?
7. Should task receipts become first-class external evidence for later audit and
   retrieval?

---

## 16. Risks

| Risk | Mitigation |
|---|---|
| PydanticAI becomes a hard dependency | Keep implementation under integration boundary; enforce import tests |
| Agent output bypasses governance | Route all mutation through canonical write/maintain paths |
| Cost runaway | Task budgets, cheap defaults, frontier allowlist |
| Prompt/schema drift | Version prompts/rubrics/schemas in every receipt |
| User loses trust in opaque agent work | Compact activity receipts with evidence drill-down |
| Dreamer overreaches into truth | Preserve candidate-only boundary and approval flow |
| Heuristic fallbacks hide model failures | Persist fallback receipts and expose diagnostics |

---

## 17. Target End State

The final system should feel like this:

1. A document, transcript, or connector event enters the hosted memory
   application.
2. Core Memory creates deterministic source/provenance scaffolding.
3. The operator harness dispatches cheap semantic agents to author fields,
   claims, entities, and candidate edges.
4. Core Memory validates and queues anything requiring judgment.
5. The operator harness dispatches stronger agents for ambiguous graph decisions
   and frontier Dreamer research.
6. The hosted operator UI shows the user what the operator did and what needs
   leadership.
7. The user approves, rejects, or redirects.
8. Core Memory applies only governed changes, leaving a full audit trail.

This makes the agent the memory operator and the user the leader. The system can
be imaginative where imagination is useful, strict where truth matters, and
cheap where scale demands it.
