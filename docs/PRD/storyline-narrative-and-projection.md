# PRD: Storyline Narrative Generation & Multi-Trajectory Future Projection

**Status:** Draft v1
**Owner area:** `runtime/dreamer/convergence.py`, `runtime/dreamer/projection.py`,
storyline overlays, projection read surfaces
**Builds on:** `agentic-semantic-task-runtime.md` (prompt/verifier substrate),
`dreamer-continuity-engine.md` (§16–§24 future projection), `myelination-reinforcement.md`
+ calibration meter (learned confidence)
**Complements:** `storylines-structural-equivalence.md` (that PRD improves narrative
*extraction*; this one improves *generation, confidence, and projection*)

---

## 0. Problem

Storylines and future projection are structurally sound but mechanically thin in
three ways, all visible in current code:

1. **Narratives are templated, not authored.** The overlay `statement` is an
   f-string — `convergence.py:114` ("N continuity threads converge across M shared
   beads: {labels}… These histories are evolving together."). That is a structural
   *description*, not a narrative.
2. **Confidence is a hand-tuned constant formula.** `convergence.py:112`
   (`0.45 + 0.08·size + 0.12·(kinds−1)`, capped 0.9) and projection
   `confidence = narrative_strength`. Nothing is *learned* from what has actually
   proven true.
3. **The light cone is shallow, single-trajectory-ish, templated, and unexposed.**
   `projection.py` projects **one hop**: a `continuation` vector + one
   `tension_resolution` fork per open tension, each with templated
   `projected_state`/`statement` text, scored by isolated formulas, and persisted
   to `dreamer-projections.jsonl` with **no HTTP read endpoint** (geometry,
   worldlines, storylines all have `/v1/memory/projection/*`; future projection
   does not).

This PRD replaces templated text with **prompt-authored, verified** narratives;
replaces the constant confidence with a **learned confidence grounded in
similarity to known truths**; and upgrades projection to a **bounded multi-hop,
multi-trajectory light cone with comparative probabilities** — all exposed over
HTTP.

---

## 1. Goals

- Narratives (storyline overlays *and* projection prose) are **authored by a
  prompt**, grounded in the structural evidence, verified, and agent-judged —
  never templated.
- Candidate confidence is **learned from similarity to already-validated truths**,
  calibrated and monitored, replacing the hand-tuned formula.
- The light cone projects **up to 3 hops per chain** and **multiple competing
  trajectories per cone**, each with a **comparative (normalized) probability** and
  the comparative rationale.
- Storyline narratives and the light cone are **served over HTTP**.

## 2. Non-Goals

- No change to backbone derivation or the **one-way rule** (interpretation never
  feeds history).
- Projection stays **advisory** — never creates goals, beads, claims, or
  auto-accepted overlays (§ dreamer §22).
- No new model stack; reuse the **semantic-task runtime** (prompt + verifier +
  receipts) already used by `runtime/dreamer/research.py`.

---

## 3. Prompt-authored narratives (replace templates)

- **Overlay narratives:** the `narrative_candidate` statement becomes a
  semantic-task output (`TASK_STORYLINE_NARRATIVE`), authored from the convergence
  / structural-equivalence evidence (member worldline labels, shared/structural
  beads, kinds, open tensions). It runs through the existing **semantic-task
  verifier** (grounded-in-evidence, no hallucinated entities), carries
  `semantic_task_refs` (receipt provenance), and still flows through the **decide
  flow** before becoming an accepted overlay. No template is the primary path.
- **Projection prose:** `projected_state` and per-vector `statement` are authored
  the same way (no f-strings).
- **Degraded mode only:** if the semantic runtime is unavailable, fall back to a
  minimal deterministic line **marked `assurance: low` / `degraded: true`** and
  **not auto-accepted** — explicitly a fallback, not the design.
- **Cadence:** authoring runs on the **Dreamer cadence (offline)**, never the hot
  path; bounded by depth/chain caps (§5) to cap token cost.

---

## 4. Learned confidence from similarity to known truths

Replace the constant formula with a **calibrated, learned** confidence.

- **"Known truths"** = the corpus of previously **accepted-and-not-later-retracted**
  overlays, **resolved** goals/outcomes, and **confirmed** projections (validated
  history).
- **Signal:** a candidate's confidence prior is a function of its **similarity to
  known truths** — embedding similarity of the candidate narrative + its structural
  signature (from the structural-equivalence work) to the validated corpus. High
  similarity to validated truths raises the prior; similarity to **rejected**
  candidates lowers it.
- **Calibration:** route the score through the existing calibration meter
  (`compute_calibration_curve`) so predicted confidence is monitored against
  realized acceptance/resolution — confidence means something measurable, not a
  magic constant.
- **Cold start:** with few truths, fall back to a **conservative prior** (and lean
  on grounding/Assembly-Depth), shifting toward the learned signal as validated
  history accumulates (mirrors the structural-equivalence "earned over time" idea).
- Applies to both overlay candidates and projection-vector probabilities (§5).

---

## 5. Light cone v2 — bounded multi-hop, multi-trajectory, comparative

- **Depth:** up to **3 hops per chain** (hard cap). Rationale (user): deeper than
  that predicts nonsense. Tunable down via env; never above 3.
- **Breadth:** project **multiple competing trajectories per cone** (not just
  continuation + one tension fork) — distinct plausible paths the storyline could
  take.
- **Comparative probability:** the trajectories are scored **comparatively** — the
  prompt ranks the whole set against each other and emits a **normalized relative
  probability** per trajectory plus the comparative rationale, rather than scoring
  each in isolation. (LLMs do comparative analysis well without the paralysis
  humans get over many options — lean on that.) `narrative_strength` /
  `attractor_strength` remain as structural inputs to the comparison; the learned
  confidence (§4) calibrates the final probabilities.
- **Max trajectories per cone (open, §8.A):** bounded but generous. Recommended
  starting cap **≈ 5** per cone, env-tunable; revisit with eval data. The cap
  exists to bound token cost and reader overload, not because the model can't
  compare more.
- **Most-likely path:** keep `narratively_most_likely_vector_id`, now chosen from
  the comparative probabilities.
- **Governance unchanged:** advisory only — `may_create_goals: False`, no
  beads/claims/auto-overlays.

---

## 6. Endpoints

- **`GET /v1/memory/projection/future`** — read the light cone (served from the
  persisted `dreamer-projections.jsonl` via `read_future_projections`; parity with
  the geometry/worldlines/storylines projection reads; `present=false` before the
  first dreamer-run).
- **`GET /v1/dreamer/projections`** — alias, mirroring the geometry endpoint pair.
- Ensure the prompt-authored overlay narratives surface through the existing
  **`GET /v1/memory/projection/storylines`** (`derive_storylines`).

---

## 7. Guardrails

1. **One-way rule.** Narratives, projections, and learned-confidence edges never
   feed backbone derivation; backbone output stays byte-identical with/without
   them.
2. **Prompt output is verified + agent-judged.** Every authored narrative passes
   the semantic-task verifier (grounded in cited evidence, no invented entities)
   **and** the decide flow before acceptance. No auto-written overlays.
3. **Projection is advisory.** No goals/beads/claims; depth ≤ 3; current-truth
   grounding (skip superseded/contradicted beads as seeds).
4. **Confidence is calibrated, not asserted.** Learned confidence is monitored by
   the calibration meter; cold-start uses a conservative prior.
5. **Cost bounded + off the hot path.** Authoring + projection run on the Dreamer
   cadence; depth/breadth caps bound token spend; retrieval/reads consume
   persisted outputs, never recompute inline.

---

## 8. Open questions

- **A. Max trajectories per light cone.** Start ≈ 5, env-tunable; settle with eval
  data (coverage vs. token cost vs. reader overload).
- **B. Similarity backbone for learned confidence:** narrative-embedding similarity
  vs. structural-signature similarity vs. both; and how to weight "similar to
  accepted" vs. "similar to rejected."
- **C. Probability semantics:** strictly normalized-to-1 across the cone, or
  independent calibrated likelihoods? (Normalized reads cleaner for comparison;
  independent survives "all unlikely" cones better.)
- **D. Verifier strictness** for projection prose (it describes *possible* futures,
  so "grounded in evidence" must allow forward speculation while still forbidding
  invented entities/claims).

---

## 9. Rollout

1. **Narrative authoring** — `TASK_STORYLINE_NARRATIVE` for overlay statements +
   projection prose, verified, decide-flow gated; degraded fallback. Removes the
   templates.
2. **Endpoints** — `GET /v1/memory/projection/future` (+ alias); surface authored
   narratives via the storylines read.
3. **Learned confidence** — similarity-to-truths calibrated confidence replacing
   the constant formulas (overlays + projection).
4. **Light cone v2** — ≤3-hop, multi-trajectory, comparative normalized
   probabilities.

---

## 10. Success criteria

- Overlay/projection text reads as authored narrative grounded in cited evidence
  (verifier-passed), with **zero** templated statements on the primary path.
- Predicted confidence is **calibrated** (meter shows predicted ≈ realized
  acceptance/resolution) and beats the old constant formula on a labeled set.
- A cone presents **multiple competing trajectories** (≤3 hops) with comparative
  normalized probabilities and a defensible most-likely path.
- The light cone is **retrievable over HTTP**; backbone output is unchanged
  (one-way-rule test green); projection creates no goals/beads/claims.
