# A Reference Evaluation Framework for Causal-Continuity Memory

Status: Reference framework (paper backbone). Synthesizes and extends the in-repo
benchmark suite (`benchmarks/`, `eval/`) into a construct-valid evaluation program.

---

## 0. One-paragraph thesis

Long-conversation memory benchmarks (LoCoMo, LongMemEval, and kin) score **multi-session
question answering** — needle retrieval plus answer accuracy. That measures the
*retrieval substrate a causal memory layer composes*, not what the layer *adds*. A pure
dense-RAG pipeline can top those benchmarks. The value of a causal-continuity memory is a
**construct those benchmarks do not contain**: surfacing the true cause a similarity ranker
buries, keeping confidence calibrated to realized usefulness, preserving a coherent
self-model and goal threads across time, and surfacing contradictions instead of
flattening them. This framework defines the capabilities, tasks, metrics, baselines, and
faithfulness controls needed to measure *that* construct — and positions the existing
LoCoMo-style harness as a **contrast condition**, not the target.

---

## 1. Construct validity: what is being measured, and why LoCoMo is a mismatch

**The IR/QA construct (LoCoMo class).** Input: a long multi-session dialogue + a question.
Output: an answer string. Score: token/semantic overlap with a gold answer, optionally
evidence precision/recall against gold turns. This is *information retrieval + reading
comprehension*. It is satisfiable by retrieve-then-read over any reasonable index.

**The causal-continuity construct (this work).** A memory layer is valuable to the degree
it (a) reconstructs *why* — the causal chain behind an outcome — not just *what was said*;
(b) carries calibrated confidence so a downstream agent can trust its own recall; (c)
preserves continuity — a stable self-model and live goal/decision threads across sessions;
(d) surfaces contradictions for adjudication rather than silently picking one side; and (e)
returns the right *storyline thread* under an agentic loop without drifting from the query.

**The mismatch, made concrete (the headline experiment).** Seed a history with a known
causal chain `outcome → mid → root` and an *adversarial distractor*: the bead whose surface
text is the closest semantic match to the query but which sits **off** the causal chain
(e.g. a runbook literally titled with the question). Pure similarity ranks:

```
distractor   score 1.48   <- closest text match, NOT the cause
mid          score 0.27
outcome      score 0.27
root         score 0.27   <- the TRUE root cause, ranked last
```

Causal traversal ranks:

```
root         influence 1.0   <- the TRUE root cause, ranked first
mid          influence 0.47
(distractor never appears — it has no causal edges)
```

That inversion is the entire argument for a causal memory layer over a reranked vector
store, and **no QA-accuracy metric scores it**. The framework's headline metric is built
on exactly this inversion (§4, Causal Survival Rate). This experiment already lives in
`benchmarks/causal/` and is the seed of the whole program.

---

## 2. Capabilities under test (C1–C5)

Each capability is a distinct claim a causal-continuity memory makes that an IR baseline
does not. The task suite (§3) isolates one capability per task; the ablations (§7) attribute
each capability to a specific mechanism.

| | Capability | The claim | Why IR/QA can't see it |
|---|---|---|---|
| **C1** | **Causal attribution** | Surfaces the true root cause over the closest-text distractor | QA scores the answer, never which edge was traversed |
| **C2** | **Confidence calibration** | `effective_confidence` predicts realized usefulness | QA has no notion of the system's confidence in its own recall |
| **C3** | **Temporal / as-of correctness** | Returns the state true *at time T*; respects supersession | QA conflates "stated somewhere" with "true now/then" |
| **C4** | **Continuity & contradiction** | Stable self-model + live threads across sessions; surfaces contradictions | QA is single-shot; contradictions are answered, not surfaced |
| **C5** | **Thread fidelity under an agentic loop** | Returns the correct causal *storyline thread* without query drift | QA returns a span, not a drift-resistant thread |

---

## 3. Task suite (existing → framework, with gaps marked)

### T1 — Causal-chain reconstruction with adversarial distractors  (C1) · **exists**
`benchmarks/causal/`. Synthetic histories with gold causal edges + a closest-text
distractor off the chain. Materialized through the **public write path** (`add_bead` +
agent-judged associations), queried via `recall(intent="causal")`; the runner reads
`root_cause_attribution.causal_paths[].edges[]` to recover the traversed edges.
- Per-case: `edge_precision/recall/f1`, `root_cause_correct`, `grounding_full`,
  `attribution_depth`, `distractor_survived`.
- Aggregate headline: `distractor_survival_rate`.

### T2 — Calibration reliability  (C2) · **gap: meter exists, scored task missing**
PRD-B ships the calibration *meter* (`/v1/myelination/calibration`) — it bins edges by
`effective_confidence` and reports realized usefulness per band + Spearman ρ + an
`auto_mode_gate`. Turn it into a *scored benchmark task*: seed histories with known
"useful vs misleading" supporting edges, accumulate validated-outcome feedback, and score
whether confidence orders usefulness. Metrics: **Spearman ρ**, **Expected Calibration
Error (ECE)**, **Brier score** of `effective_confidence` vs realized usefulness; pass gate
ρ ≥ 0.70 (the PRD-B threshold) and high-band usefulness ≥ 0.80. (Build note: the meter's
X-axis must be real `effective_confidence = clamp(judge_prior + manifest_bonus)`, not a
flat prior — see PRD-B calibration fix.)

### T3 — Temporal/as-of + contradiction-update  (C3, C4) · **partial: locomo_like buckets**
`benchmarks/locomo_like/` already buckets current-state, historical/as-of,
contradiction/update, entity/coreference. Reframe scoring away from answer-token-F1 toward
**correct-state-selection**: did recall return the state true *as-of* the query time, and
did it *respect supersession* (not surface a retracted/superseded bead as active truth)?
Metrics: as-of accuracy, supersession-respect rate, contradiction-surfaced rate (the system
flags the conflict rather than silently choosing).

### T4 — Longitudinal continuity lift + self-model stability  (C4) · **implemented: harness slice**
`eval/longitudinal_benchmark_v2.py` already compares a memory/dreamer cohort against a
no-memory baseline (`core_with_dreamer_vs_no_memory_lift`). The suite-level T4 harness now
adds **self-model drift** (the PRD-B drift meter) as a stability metric over the run.
Metrics: continuity **lift > 0** vs no-memory; drift score = 0 (no
ungrounded/contradictory identity revisions) across the window; goal-thread persistence
rate.

### T5 — Thread fidelity under the agentic recall loop  (C5) · **implemented: deterministic harness slice**
PRD-E's iterative recall loop (semantic seed → reward-elected causal expansion → per-hop
re-evaluation against the *original* query → answerability/stop gate) is the surface this
task scores. Given a query with a gold storyline thread + off-thread distractor beads, score
**thread precision/recall** of the returned segment and an **LLM-judge answerability** call
(did the assembled thread contain sufficient evidence to answer correctly?). Crucially,
include "query-drift" probes: a thread that wanders to a higher-similarity but off-query
subgraph must be penalized — the metric IR can't express. The suite-level T5 harness now
ships a deterministic local proxy around `trace_request()` and storyline selection; the
external LLM judge adapter remains a future extension.

---

## 4. Metrics

**Design rule:** every headline metric must be one a pure vector-RAG cannot game. If a
retrieve-then-rerank baseline can match it, it is measuring the substrate, not the layer.

| Metric | Task | Definition | Pass target |
|---|---|---|---|
| **Causal Survival Rate (CSR)** | T1 | fraction of adversarial cases where the gold root cause outranks every closest-text distractor | headline; report vs baselines |
| Edge-F1 / grounding-full | T1 | traversed vs gold causal edges; full-recall path to root | report |
| Calibration ρ / ECE / Brier | T2 | rank-corr + calibration error of `effective_confidence` vs realized usefulness | ρ ≥ 0.70, high-band ≥ 0.80 |
| As-of accuracy / supersession-respect | T3 | correct state at time T; retracted beads not surfaced as active | report; supersession ≥ 0.95 |
| Contradiction-surfaced rate | T3/T4 | conflicts flagged for review, not silently resolved | report (invariant: never auto-resolved) |
| Continuity lift / drift | T4 | memory-cohort vs no-memory; self-model drift score | lift > 0; drift = 0 |
| Thread fidelity / answerability | T5 | storyline-thread P/R + LLM-judge sufficiency, drift-penalized | report |
| Recall@5 / MRR / latency / determinism | all | the IR substrate guardrails (KPI targets) | Recall@5 ≥ 0.60, MRR ≥ 0.50, det. top-5 |

The IR guardrails (last row) are *necessary-not-sufficient*: a causal layer must not be
*worse* at plain retrieval, but plain retrieval is not the claim.

---

## 5. Baselines (the comparison set the paper stands on)

The differentiator is only visible against comparators. Run every task across:

1. **BM25 / lexical** — floor.
2. **Dense vector RAG** — the pure-similarity system the inversion (§1) is defined against.
3. **Long-context stuffing** — full transcript in the model context, no memory layer
   (controls for "is a memory layer needed at all at this horizon?").
4. **External memory systems** — e.g. Mem0 / Zep / Letta-class, via the
   `BenchmarkAdapter` protocol — for positioning credibility (these report LoCoMo numbers;
   show they do *not* clear the causal/calibration bar).
5. **Core Memory (full)** + **ablations** (§7).

On T1, baselines 1–4 are expected to fail CSR by construction (they have no causal edges);
that failure *is* the result. On the IR guardrails they should be competitive — the point
is parity on retrieval, dominance on causality/calibration/continuity.

---

## 6. Faithfulness & contamination controls (credibility backbone)

A reviewer's first question is "did you cheat?" The suite answers it mechanically via
`benchmarks/contracts.py::BenchmarkShortcutFlags`. Every reported run carries
`is_faithful` and disqualifies the result if any shortcut is set:

- `synthetic_crawler_updates` — associations must come from the real agent-judged crawler,
  not hand-written edges.
- `synthetic_temporal_edges` — temporal structure must emerge from real ingestion order.
- `bead_direct_ingest` — histories materialize through the **public write path**
  (`emit_turn_finalized` / `add_bead`), never by injecting beads/edges into the index.
- `oracle_gold_used` — gold ids/edges must not leak into retrieval-time logic.
- `benchmark_aware_answer_prompt` — no prompt may mention the benchmark or its answer shape.

This is the property that lets the paper claim the numbers reflect the *deployed* system,
not a benchmark-tuned path. Keep it as a hard CI gate.

---

## 7. Ablation protocol (the experiments that make the paper)

Hold the task suite fixed; toggle one mechanism at a time; attribute each capability to its
mechanism. Expected drops in **bold**.

| Configuration | T1 CSR | T2 calib | T3 as-of | T4 lift | T5 thread |
|---|---|---|---|---|---|
| Core Memory (full) | high | pass | high | >0 | high |
| − causal traversal (similarity only) | **collapse** | – | – | – | **collapse** |
| − myelination backpressure (`bonus_by_edge_key` off) | drop | **flat/ρ↓** | – | drop | drop |
| − validated-outcome reward (marginal only) | – | **ρ↓** | – | drop | – |
| − dreamer | – | – | – | **lift→0** | – |
| − supersession/temporal filter | – | – | **drop** | – | – |
| − agentic recall loop (one-shot recall) | – | – | – | – | **drift↑** |

Each row is one figure in the paper. The diagonal of bolded drops *is* the argument:
each mechanism owns a capability no other mechanism recovers.

---

## 8. Datasets

- **Synthetic, checked-in (primary).** Per-task fixtures + gold under `benchmarks/<task>/`,
  fully reproducible, zero external deps (`JsonFileBackend`). Controlled construct validity:
  the gold causal chain and the adversarial distractor are *known*, so CSR is unambiguous.
- **Real-data slice (ecological validity).** LoCoMo / LongMemEval via `BenchmarkAdapter`
  (`load_conversations`, `score_answer`, `score_evidence`). Reported separately and labeled
  as a *contrast* condition — never as the target metric. (See `docs/benchmarks/locomo/
  baselines.md`: do not present the local proxy as a LoCoMo leaderboard result.)

Distractor construction (T1/T5) must be principled and disclosed: distractors are selected
as the top-similarity off-chain bead under the same embedding the dense baseline uses, so
the inversion is not an artifact of a weak distractor.

---

## 9. Reporting & reproducibility

- **Report schema** (extend the existing runner output): `run_at`, `git_sha`, `backend`,
  `faithfulness` (the flag block), per-task headline + per-bucket breakdown, per-case rows.
- **Determinism** (KPI targets): identical ordered top-5 across 5 repeated runs; latency
  bound. A non-deterministic result is not a publishable result.
- **Provenance**: synthetic fixtures + the public-write-path materialization + faithfulness
  flags mean any reviewer can reproduce a number from the committed repo with no network.

---

## 10. Threats to validity (state them; reviewers will)

1. **Synthetic bias.** Hand-authored causal chains may be easier than messy real causality.
   Mitigation: the real-data adapter slice + principled, disclosed distractor construction.
2. **Judge variance (T5).** LLM-as-judge answerability is noisy. Mitigation: fixed judge +
   rubric, report agreement, keep the deterministic edge/thread metrics as the primary
   signal and the judge as secondary.
3. **CSR construction sensitivity.** CSR depends on the distractor being a genuine
   strongest-similarity competitor; disclose the selection embedding and show CSR is robust
   to distractor count.
4. **Calibration coverage.** Calibration ρ is unstable at low event volume; report
   `insufficient_data` honestly and gate on a minimum N (the PRD-B `MIN_HITS` analogue).
5. **Positioning.** Do not claim a LoCoMo SOTA number; claim a *new construct* with a
   construct-valid benchmark and show baselines fail it while staying competitive on the IR
   guardrails.

---

## 11. Paper-build checklist (what to land before submission)

- [ ] T1 CSR table: Core Memory vs the 4 baselines (exists — extend `benchmarks/causal/`).
- [ ] T2 calibration task: scored ρ/ECE/Brier (build on the PRD-B meter).
- [ ] T3 as-of/supersession scoring reframe of `locomo_like` buckets.
- [x] T4 longitudinal lift + drift harness slice.
- [x] T5 thread-fidelity deterministic harness slice.
- [ ] Ablation matrix (§7) run end-to-end with faithfulness flags clean.
- [ ] Real-data adapter slice (LoCoMo/LongMemEval) as the contrast condition.
- [ ] One reproducibility appendix: `python -m benchmarks.<task>.runner` → committed report.

The minimum publishable core is **T1 (CSR) + T2 (calibration) + the ablation matrix** with
clean faithfulness flags and the dense-RAG baseline — that alone substantiates "a construct
LoCoMo doesn't measure, with a benchmark that isolates it." T3–T5 deepen it.
