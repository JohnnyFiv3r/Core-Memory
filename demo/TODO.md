# Post-Demo TODO

Items identified during PydanticAI integration and demo build. These are shortcuts taken for the demo that need proper implementation.

## 1. Replace echo-based `because` with LLM extraction

**Current behavior:** The `because` field on every bead is set to the user's raw message text. This passes the promotion quality gate every time, meaning decisions and lessons always promote instantly on the same turn.

**Problem:** A weak statement like "maybe we should use Redis" would promote as a decision immediately. The `because` field should contain extracted causal reasoning, not echoed input.

**Fix:** Add a Haiku/cheap-model call (same pattern as bead type classifier) that either extracts structured reasoning from the user message or returns empty when the input doesn't contain real causal reasoning. Empty `because` means the bead stays `open` → `candidate` and earns promotion through reinforcement from later turns.

**Impact:** Decisions and lessons will sometimes stay `open` or `candidate` — that's the intended behavior. Promotion becomes earned, not automatic.

**Files:** `core_memory/runtime/engine.py` (`_default_crawler_updates`, `_ensure_turn_creation_update`)

## 2. Goal lifecycle — resolution mechanism

**Current behavior:** Goals classify correctly and stay as `candidate` indefinitely. There is no way to resolve or close a goal.

**Problem:** When a later turn says "we finished the OAuth2 migration", nothing links that outcome to the original goal bead or transitions the goal to a resolved state.

**Fix:** Build a goal resolution pass that:
- Detects when a new `outcome` bead relates to an open `goal` bead
- Creates an association linking the outcome to the goal
- Transitions the goal status (e.g. `candidate` → `promoted` or a new `resolved` state)

This could be LLM-assisted (ask "does this turn resolve any open goals?") or heuristic (keyword/semantic matching between outcomes and goals).

**Files:** `core_memory/runtime/engine.py`, `core_memory/policy/promotion_contract.py`

## 3. Association relationship types

**Current behavior:** All associations created from `association_preview` have relationship type `shared_tag`. This is the store's quick-match heuristic.

**Problem:** The relationship types should be more descriptive — `caused_by`, `led_to`, `reinforces`, etc. The schema supports 28 relationship types but only `shared_tag` is used in practice through the PydanticAI path.

**Fix:** Either use the LLM to classify the relationship type when queuing associations, or improve the store's preview logic to infer richer relationship types from bead content.

**Files:** `core_memory/runtime/engine.py` (`_queue_preview_associations`), `core_memory/persistence/store.py` (association preview logic)

## 4. Bead type classifier — questions misclassified as precedent

**Current behavior:** The classifier prompt tells the LLM to classify questions as `context`, but some question phrasings like "Why did we decide to always benchmark..." get classified as `precedent` because the LLM interprets "did we decide" as referencing a past pattern.

**Problem:** Questions should always classify as `context` — the user is retrieving, not declaring. The `precedent` type auto-promotes, so misclassified questions get promoted immediately.

**Fix:** Strengthen the classifier prompt or add a pre-check: if the user message ends with `?` or starts with a question word, force `context` before calling the LLM.

**Files:** `core_memory/policy/bead_typing.py`
