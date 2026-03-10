# Retrieval Without Prompt Dependence: Problem Report

## Executive Summary

Current retrieval has improved significantly on deterministic metrics and structural grounding in the KPI set, but it still shows query-phrase sensitivity that cannot be solved by “better prompting.”

This report documents:
- what is working,
- where behavior is still brittle,
- concrete examples,
- measured metrics,
- and the non-prompting remediation path.

---

## Why This Matters

A memory/search system should not require users to phrase queries in a specific way. If the system only works when prompted carefully, it is not robust retrieval—it is prompt steering.

The target behavior is:
- same intent, varied phrasing -> still returns grounded results,
- deterministic outputs,
- explicit confidence/grounding states when insufficient structure exists.

---

## Current Observed Metrics

From recent eval runs:
- `recall_at_5`: 1.0
- `mrr`: 0.75
- `deterministic`: true
- `low_info_citation_rate`: 0.0 (after title hygiene pass)
- `causal_grounding_rate`: 1.0 (after targeted structural backfill on KPI cases)

Interpretation:
- Retrieval/ranking can find relevant content.
- Structural grounding can be enforced when links exist.
- Remaining issue is robustness under query phrasing and mixed bead typing.

---

## Evidence: Query-Dependent Variance (Non-KPI Spot Checks)

### Query A
`why did we make promotion agent-authoritative?`

Observed:
- selected route: `why`
- structural chain present (`edge_counts=[1]`)
- citations mostly `context/context`
- answer still conservative (not strongly grounded narrative)

### Query B
`what changed in the promotion workflow?`

Observed:
- selected route: `why` (hint: `what_changed`)
- structural chains present (`[1,1,1]`)
- citations include evidence + decision
- answer is clearly grounded/causal

### Query C
`remember the structural sync pipeline work`

Observed:
- selected route: `why` (quality policy)
- structural chain present (`[1]`)
- citations outcome + lesson
- response remains conservative

### Diagnosis from examples
Same domain, same memory corpus, different query phrasing -> meaningfully different answer quality.
That is expected to some extent, but current variance is too high for dependable retrieval UX.

---

## Root-Cause Breakdown

## 1) Structural availability is now partially fixed, but localized
The immutable sync pipeline works and targeted backfill fixed KPI-case grounding. However, structural coverage is still uneven outside curated cases.

## 2) Selector robustness is improved but not fully intent-normalized
Structural constraint for causal intent is in place, but query text still influences which candidate paths become dominant.

## 3) Bead type/title quality still influences answer interpretation
Even with low-info title cleanup, some beads remain semantically weakly typed (`context` where stronger types would help reasoning).

## 4) KPI set is still too small
Two cases are enough for regression smoke, not enough for confidence in phrase-robust generalization.

---

## Problem Statement (Precise)

The system currently achieves high deterministic retrieval scores on a small fixture and can enforce structural grounding where links exist, but it does not yet provide phrase-robust semantic equivalence for user-intent retrieval across varied wording.

This is a retrieval robustness gap, not a prompting gap.

---

## What Should Not Be Used As a Fix

- Asking users to rephrase queries.
- Prompt templates as primary retrieval control.
- Adding non-deterministic LLM query rewriting.

These can mask but not solve the retrieval robustness problem.

---

## Non-Prompting Fix Path (Recommended)

## A) Expand structural coverage deterministically
- Continue constrained backfill (targeted mode first).
- Keep immutable edge contract strict.

## B) Add phrase-robust retrieval checks
- Expand KPI fixture to 10–15 cases with paraphrase variants per intent.
- Score same-intent query clusters for variance.

## C) Add selector invariant for causal intents
- Already added: structural constraint metadata.
- Next: add a “semantic equivalence sanity check” across paraphrase fixtures.

## D) Improve type-quality where safe
- Normalize mis-typed high-value beads in curated pass (decision/evidence/outcome where objectively justified).
- Keep audit trail for type corrections.

---

## Immediate Next Metrics to Add

1. **Paraphrase consistency score** (same intent family, top-N overlap)
2. **Causal route stability** for causal-intent variants
3. **Grounded citation type mix** per query family

These directly measure “works without good prompting.”

---

## Recommended Next Sprint (No complexity creep)

1. Build a 12-case paraphrase KPI pack (4 intents × 3 phrasings each).
2. Run targeted structural backfill only for uncovered intent families.
3. Run deterministic eval and report:
   - recall/mrr
   - grounding
   - paraphrase consistency
4. Only then tune weights if needed.

---

## Bottom Line

We are past infrastructure uncertainty: structural pipeline + deterministic selection controls are in place.

The remaining gap is **query-phrase robustness**. The fix is not prompting; it is deterministic coverage + fixture expansion + consistency testing.
