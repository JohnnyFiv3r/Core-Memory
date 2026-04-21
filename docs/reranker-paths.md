# Reranker Paths: Feature-Parity Matrix

**Fix ID:** F-R4 (P1)
**Date:** 2026-04-20

Core Memory has two reranker code paths. This document lists every feature computed in each, the weights applied, and which code paths invoke them.

---

## When Each Reranker Runs

| Reranker | File | Invoked by | Purpose |
|----------|------|-----------|---------|
| **Structural reranker** | `retrieval/rerank.py` → `rerank_candidates()` | `retrieval/pipeline/canonical.py` (search/execute) | Second-stage reranking after hybrid_lookup. Focuses on structural graph features, domain alignment, and query-term coverage. |
| **Evidence reranker** | `retrieval/evidence_scoring.py` → `rerank_semantic_rows()` | `retrieval/tools/memory_reason.py` (causal trace) | Reranks semantic lookup results for causal/evidence queries. Focuses on claim state, entity matching, temporal fit, and supersession. |

**Key distinction:** The structural reranker runs on `search` and `execute` intents. The evidence reranker runs on `trace` and causal-intent queries routed through `memory_reason.py`.

---

## Feature-Parity Matrix

### Features computed

| Feature | Structural (`rerank.py`) | Evidence (`evidence_scoring.py`) | Notes |
|---------|:---:|:---:|-------|
| Fused score (semantic + lexical) | `W_FUSED = 0.50` | `semantic` + `lexical` (separate weights) | Structural uses pre-fused score; evidence decomposes |
| Structural quality (decision+evidence+outcome chain) | `W_STRUCTURAL = 0.20` | `structural` (context_bias_score) | Different computation — structural uses chain_features |
| Edge support (grounding + count) | `W_EDGE_SUPPORT = 0.15` | — | Evidence reranker has no edge support feature |
| Query-term coverage | `W_COVERAGE = 0.10` | `lexical` overlap | Different implementation |
| Incident match | `W_INCIDENT = 0.05` | — | Evidence reranker has no incident matching |
| Domain alignment | +0.08 / -0.12 penalty | — | Structural only (config-driven since F-R1) |
| Bridge pattern bonus | up to +0.22 | — | Structural only |
| Two-hop support | +0.15 * (count/4) | — | Structural only |
| Grounding structural edge bonus | +0.22 | — | Structural only |
| Low-info penalty | `W_PENALTY = 0.10` | — | Structural only |
| Superseded penalty | 0.6 * low_info + 0.4 * superseded | -0.28 to -0.71 | Both, different scales |
| Claim state score | — | `claim` weight (intent-dependent) | Evidence only |
| Entity match score | — | `entity` weight (intent-dependent) | Evidence only |
| Temporal fit | — | `temporal` weight (intent-dependent) | Evidence only |
| Recency score | — | `recency` weight (intent-dependent) | Evidence only |
| Current truth bonus | — | +0.08 to +0.30 | Evidence only |
| Conflict penalty | — | -0.20 to -0.57 | Evidence only |
| Retrieval value bonus | — | from bead field | Evidence only |
| Myelination bonus | — | from bead field | Evidence only |

### Intent-dependent weights (evidence reranker only)

| Weight | `remember` | `causal` | `when` | Default |
|--------|-----------|---------|--------|---------|
| `semantic` | 0.26 | 0.30 | 0.24 | 0.28 |
| `lexical` | 0.16 | 0.12 | 0.12 | 0.14 |
| `claim` | 0.26 | 0.10 | 0.10 | 0.14 |
| `entity` | 0.18 | 0.06 | 0.06 | 0.08 |
| `temporal` | 0.10 | 0.10 | 0.34 | 0.10 |
| `structural` | 0.04 | 0.24 | 0.08 | 0.18 |
| `recency` | 0.00 | 0.08 | 0.06 | 0.08 |

### Intent-dependent weights (structural reranker)

| Weight | `remember` | `causal` | `what_changed` | `when` | Default |
|--------|-----------|---------|---------------|--------|---------|
| `W_STRUCTURAL` | 0.10 | 0.24 | 0.18 | 0.10 | 0.20 |
| `W_EDGE_SUPPORT` | 0.08 | 0.17 | 0.14 | 0.08 | 0.15 |
| `W_COVERAGE` | 0.20 | 0.08 | 0.15 | 0.22 | 0.10 |
| `W_INCIDENT` | 0.10 | 0.06 | 0.07 | 0.08 | 0.05 |

---

## Differences Flagged for Future Unification

| Difference | Impact | Recommendation |
|-----------|--------|----------------|
| Supersession penalty scale | Structural: max ~1.0, Evidence: max ~0.71 | Align scales in P2 |
| Edge support feature | Structural has it, evidence doesn't | Add to evidence reranker |
| Domain alignment | Structural only | Not needed for evidence (causal queries are domain-agnostic) |
| Claim state scoring | Evidence only | Could improve structural for `remember` intent |
| Entity matching | Evidence only | Could improve structural for entity-specific queries |
| Weight values for shared intents | Different values for `causal` structural weight (0.24 vs 0.24) | Already aligned for causal |

---

## Tie-Break Policy

Both rerankers use deterministic tie-breaking:

- **Structural:** `rerank_score > fused_score > sem_score > lex_score > bead_id`
- **Evidence:** `rank_score > bead_id` (via stable sort)

This preserves the deterministic causal recall claim (replay hashes, explicit tie-breaks).
