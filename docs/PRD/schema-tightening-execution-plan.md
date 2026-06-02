# Execution Plan: Schema Tightening + Authoring Pass

**Status:** Ready for implementation  
**Scope:** `JohnnyFiv3r/Core-Memory`  
**Related PRD:** `docs/PRD/prdcmentityenrichment.md`

The audit confirms no dropped field is blocking retrieval today — the gate was compensating for weak authoring, not load-bearing. Everything can be sequenced cleanly without a migration lock. Order follows CLAUDE.md layering law: schema → persistence → policy → retrieval → runtime → integrations → tests.

---

## PR Split

- **PR A** — Phases 1–8 + Phase 12: schema slim + projection + hygiene + agent instructions + test cascade
- **PR B** — Phases 9–10: engine bypass fix + auto-linking (runtime correctness, independent of schema)
- **PR C** — Phase 11: `validation_warnings` status promotion (can ride with A or B)

---

## Phase 1 — Schema layer (`schema/models.py`)

### Fields to remove from `Bead` dataclass

| Field | Lines | Reason |
|---|---|---|
| `retrieval_eligible: bool` | 525 | Gate removed — every bead is indexed |
| `retrieval_title: Optional[str]` | 526 | Redundant if title is authored well |
| `retrieval_facts: list` | 527 | Folds into `supporting_facts` |
| `topics: list` | 530 | Identical treatment to `entities` everywhere; collapse |
| `authority: str` | 510 | Never read by retrieval or scoring |
| `incident_keys` … `time_keys` | 531–536 | Dreamer-internal only |
| `cause_candidates`, `effect_candidates` | 542–543 | Dreamer-internal only |
| `mechanism`, `impact_level`, `uncertainty` | 556–558 | Never read |
| `what_almost_happened` … `assumption` | 561–564 | Never read |
| `links: dict` | 513 | Redundant with associations for conversational ingest |

### Fields to keep

`id`, `type`, `title`, `created_at`, `session_id`, `summary`, `detail` (keep as optional overflow; stop authoring as triplication), `scope`, `tags`, `status`, `recall_count`, `last_recalled`, `source_turn_ids`, `turn_index`, `prev_bead_id`, `next_bead_id`, `entities`, `entity_ids`, `because`, `supporting_facts`, `evidence_refs`, `state_change`, `observed_at`, `recorded_at`, `effective_from`, `effective_to`, `validity` (deprecated, keep for migration), `supersedes`, `superseded_by`, `claims`, `claim_updates`, `interaction_role`, `memory_outcome`, all promotion fields (consolidated, see below).

### Consolidate promotion fields

Move these into an adjacent group in the dataclass (not a sub-object — migration cost isn't worth it):

```python
# Promotion (consolidated)
promotion_state: Optional[str] = None
promotion_locked: bool = False
promotion_score: float = 0.0
promotion_reason: Optional[str] = None
promotion_decided_at: Optional[str] = None
promotion_marked_at: Optional[str] = None
promotion_decision_turn_id: Optional[str] = None
```

### Other `models.py` changes

- Remove `Authority` enum (lines 83–88) — nothing reads it after this change
- Remove `is_retrieval_rich()` and `validate_retrieval_eligibility()` methods from `Bead` (lines 576–598)
- In `_normalize_bead_payload()` (lines 279–350): remove normalization branches for all dropped fields. For legacy on-disk beads that carry `retrieval_eligible`, map it silently to nothing (don't crash, don't preserve).

---

## Phase 2 — Projection layer (`schema/bead_projection.py`)

### Update `_LIST_FIELDS` tuple

Remove: `retrieval_facts`, `topics`, `decision_keys`, `goal_keys`, `action_keys`, `outcome_keys`, `time_keys`, `evidence_refs`, `cause_candidates`, `effect_candidates`.

Updated tuple:

```python
_LIST_FIELDS = (
    "summary",
    "because",
    "supporting_facts",
    "entities",
    "entity_ids",
    "evidence_refs",
    "tags",
)
```

### Update `build_retrieval_text()` (line 46)

Remove `retrieval_title` preference. Change:

```python
# Before
title = str(bead.get("retrieval_title") or bead.get("title") or "")

# After
title = str(bead.get("title") or "")
```

The claims section (lines 77–95) stays unchanged.

---

## Phase 3 — Policy/hygiene layer (`policy/hygiene.py`)

### Remove entirely

- `classify_bead_richness()` (lines 290–302)
- `can_be_retrieval_eligible()` (lines 305–317)

### Simplify `enforce_bead_hygiene_contract()` (lines 320–343)

Remove:
- `richness = classify_bead_richness(out)`
- `out["bead_richness"] = richness`
- The entire `if richness == "LOW": ... return out` branch
- `out["retrieval_eligible"] = ...` line

After change, the function only: normalizes title, sets `summary` default, ensures temporal minimum surface (`session_id`, `source_turn_ids`, `prev_bead_id`).

### Replace `extract_entities()` (lines 223–238)

The current capitalized-token regex (`re.findall(r"\b[A-Za-z][A-Za-z0-9_.:/-]{2,}\b")`) is the root cause of `Making`/`That` captures and missed `charity race`/`mental health`/`awareness`. Replace with a pure-Python noun-phrase heuristic (no spaCy dependency):

```python
def extract_entities(text: str) -> list[str]:
    """Extract salient noun-phrases from text. Heuristic fallback; LLM path preferred."""
    import re

    STOPWORDS = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
        "been", "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "shall", "can", "that",
        "this", "these", "those", "there", "here", "what", "when", "where",
        "which", "who", "whom", "how", "why", "all", "any", "each", "every",
        "both", "few", "more", "most", "other", "some", "such", "than",
        "then", "just", "so", "yet", "nor", "not", "its", "it", "he", "she",
        "they", "we", "you", "i", "my", "your", "his", "her", "our", "their",
        "making", "getting", "going", "said", "says", "got", "get", "let",
        "sounds", "great", "good", "nice", "really", "very", "quite",
    }

    txt = (text or "").strip()
    tokens = re.split(r"[\s,;:!?\"'()\[\]{}]+", txt)
    tokens = [t.strip(".-") for t in tokens if len(t.strip(".-")) >= 3]

    phrases = []
    i = 0
    while i < len(tokens):
        if tokens[i].lower() in STOPWORDS:
            i += 1
            continue
        # Try to extend into a 2–3 word phrase
        phrase_tokens = [tokens[i]]
        j = i + 1
        while j < len(tokens) and j < i + 3:
            if tokens[j].lower() in STOPWORDS:
                break
            phrase_tokens.append(tokens[j])
            j += 1
        if len(phrase_tokens) > 1:
            phrases.append(" ".join(phrase_tokens))
        phrases.append(tokens[i])
        i += 1

    seen = set()
    out = []
    for p in phrases:
        pk = p.lower()
        if pk in seen or pk in STOPWORDS or len(p) < 3 or p.isdigit():
            continue
        seen.add(pk)
        out.append(p)

    return out[:20]
```

The LLM path in `bead_judge.py` supersedes this when a chat provider is configured — this is purely the fallback.

---

## Phase 4 — Agent instructions (highest-priority gap)

### `core_memory/policy/bead_judge.py`

The prompt (lines ~18–58) explicitly instructs `retrieval_eligible`, `retrieval_title`, `retrieval_facts`. Complete rewrite of the judge prompt:

**Remove all instructions for:** `retrieval_eligible`, `retrieval_title`, `retrieval_facts`, `topics`, `*_keys`, `cause_candidates`, `effect_candidates`, `mechanism`, `impact_level`, `uncertainty`, contrast fields.

**New prompt instructions:**

| Field | Instruction |
|---|---|
| `title` | Specific, retrieval-optimized, 15–60 chars. Not a generic description. Not the raw utterance. A phrase someone would search for. Bad: `"Casual conversation"`. Good: `"Mental health fundraising plan discussed"`. |
| `summary` | 1–3 bullet strings, **distinct from title**. Each bullet carries a different facet: what happened, why it matters, what was decided/felt/changed. Never repeat the title verbatim. Never copy the raw utterance. **summary ≠ title ≠ detail — if they're the same, the judge failed.** |
| `entities` | Salient noun-phrases (not just proper names). Include multi-word concepts: `charity race`, `mental health awareness`, `fundraising plan`. Exclude: speaker names used only as address tokens, filler words, turn-opening phrases. Cap at 12. |
| `because` | Causal reason this bead matters to memory. 1–3 items. Empty if genuinely no causal grounding. |
| `supporting_facts` | Concrete grounded statements (replaces `retrieval_facts`). Only factual assertions, not summaries. Empty if no facts to extract. |
| `claims[]` | Only where the turn asserts a state that participates in a supersession chain (`"user prefers X"`, `"decision is Y"`, `"user is located at Z"`). Not a dumping ground. |
| `state_change` | Only when something explicitly changed from a prior state. `{from: "...", to: "..."}`. |

### `core_memory/integrations/mcp/core-memory-agent-guide.md`

The file currently gives no guidance on entity quality or summary distinctness. Add to the `capture` tool section:

- Entities should include multi-word noun-phrases (`mental health`, `fundraising plan`), not just proper names
- `summary` bullets must carry distinct signal from the title — not a restatement
- `because` and `supporting_facts` are what make a bead findable by later recall; empty `because` means the bead carries no causal signal

### `core_memory/association/crawler_contract.py` (lines 196–230)

Remove all mention of `retrieval_eligible`, `retrieval_title`, `retrieval_facts`, `topics`, `*_keys` from the documented field list. Update the `beads_create` schema string (line 216):

```
beads_create: list[{
    type, title, source_turn_ids,
    summary?, because?, supporting_facts?,
    entities?, evidence_refs?, state_change?,
    claims?, observed_at?, effective_from?, effective_to?,
    supersedes?, superseded_by?, turn_index?, prev_bead_id?
}]
```

---

## Phase 5 — Persistence write path (`persistence/store_add_bead_ops.py`)

The bead construction dict (lines 48–67) defaults `authority: "agent_inferred"` and includes `links`. Remove both entries from the dict.

No other changes needed — dropped fields that arrive via `**kwargs` are silently ignored once removed from the dataclass normalization in `models.py`.

---

## Phase 6 — Crawler contract (`association/crawler_contract.py`)

### `_normalize_creation_rows()` (lines 130–182)

Remove:
- `"retrieval_eligible": bool(r.get("retrieval_eligible", False))` (line 155)
- `"retrieval_title": str(...)[:200] or None` (line 156)
- `"retrieval_facts": [...][:12]` (line 157)
- `"topics": [...][:20]` (line 159)

Merge topics into entities at normalization:
```python
"entities": list(dict.fromkeys(
    [str(x) for x in (r.get("entities") or []) if str(x)] +
    [str(x) for x in (r.get("topics") or []) if str(x)]
))[:20],
```

Remove the eligibility guard (lines 172–174):
```python
# Remove this block:
if row.get("retrieval_eligible") and not can_be_retrieval_eligible(row):
    row["retrieval_eligible"] = False
```

Remove `retrieval_eligible=`, `retrieval_title=`, `retrieval_facts=`, `topics=` keyword arguments from the `add_bead()` call (lines 517–543).

### Tag-overlap edge fix

In `preview.py` (around lines 77–85), the `shared_tag_overlap` reason fires when beads share tags. The root cause is run-level tags (`sample:`, `session:`, `locomo:`) being written to every bead. Fix at normalization: in `_normalize_creation_rows()`, strip any tag matching run-level patterns:

```python
"tags": [
    str(x) for x in (r.get("tags") or [])
    if str(x) and not re.match(r"^(sample|session|locomo|run|benchmark)[:_-]", str(x))
][:10],
```

---

## Phase 7 — Lexical retrieval (`retrieval/lexical.py`)

Remove from `_LIST_ANCHOR_FIELDS` (lines 26–39): all 6 `*_keys` families, `cause_candidates`, `effect_candidates`, `topics` (folded into entities).

Updated:
```python
_LIST_ANCHOR_FIELDS = (
    "entities",
    "entity_ids",
    "supporting_facts",
    "evidence_refs",
)
```

Remove `"topics"` from `FIELD_WEIGHTS` dict (now folded into entities).

---

## Phase 8 — Visible corpus (`retrieval/visible_corpus.py`)

Line 35 has an advisory comment about `retrieval_eligible` being reserved for a future phase. Remove it — the field is gone. No logic change needed; `_admit()` already doesn't filter on it.

---

## Phase 9 — Runtime engine: fix crawler_updates bypass (`runtime/engine.py`)

### The bypass (line 317)

```python
# Current — returns without validation when required=False (observe mode)
if isinstance(reviewed, dict) and reviewed:
    if required:
        ...validate...
    return dict(reviewed), gate  # ← skips validation in observe mode
```

Fix: validate in all modes; only the consequence differs.

```python
if isinstance(reviewed, dict) and reviewed:
    ok, code, details = validate_agent_authored_updates(reviewed, max_create_per_turn=max_create_per_turn)
    gate["validation"] = details
    if not ok:
        gate["error_code"] = code
        if not required or fail_open:
            logger.warning("crawler_updates validation failed (mode=%s): %s", mode, code)
            # still use reviewed payload, just warn
        else:
            gate["blocked"] = True
            return None, gate
    return dict(reviewed), gate
```

### Wire authoring pass when `crawler_updates` is supplied

Today `judge_bead_fields` runs only on the fallback path (no `crawler_updates`). Fix: always run `judge_bead_fields` to produce a baseline, then merge with caller payload (caller values win for any field provided; judge fills gaps). Implementation point: around lines 283–317, call `judge_bead_fields` unconditionally before the `reviewed` check, then merge.

---

## Phase 10 — Auto-link `prev_bead_id`/`next_bead_id`

The hygiene stub at `hygiene.py:331-332` is a no-op. Auto-linking must happen in `store_add_bead_ops.py`, inside the existing `store_lock` context (lines 98–174), after `index["beads"][bead["id"]] = bead`:

```python
# Auto-link temporal chain within session
if resolved_session_id:
    session_beads = sorted(
        [b for b in index["beads"].values()
         if b.get("session_id") == resolved_session_id and b["id"] != bead_id],
        key=lambda b: b.get("created_at", ""),
        reverse=True,
    )
    if session_beads:
        prev = session_beads[0]
        bead["prev_bead_id"] = prev["id"]
        prev["next_bead_id"] = bead_id
        index["beads"][prev["id"]] = prev
```

No new locking needed — runs inside the existing `store_lock` context.

---

## Phase 11 — `validation_warnings`: act instead of record

**File: `core_memory/persistence/store_validation_helpers.py`**

After writing warnings (line 135), set status to `"candidate"` instead of `"open"` so validation-failing beads are flagged for review without blocking creation:

```python
if issues:
    bead["validation_warnings"] = issues
    if not bool(store.strict_required_fields) and bead.get("status") == "open":
        bead["status"] = "candidate"  # warn-and-flag rather than silent pass
```

`candidate` status is admitted by visible_corpus, so these beads remain retrievable but are visibly incomplete.

---

## Phase 12 — Test cascade

### Complete rewrites (logic changes)

| File | Change |
|---|---|
| `tests/test_bead_field_judge.py` | Remove assertions about `retrieval_eligible`, `retrieval_title`, `retrieval_facts`. Replace with: `title` is specific (not generic), `entities` contains noun-phrases, `supporting_facts` populated. |
| `tests/test_agent_authored_contract_validation.py` | Remove 8 retrieval field validation cases; add cases for engine bypass fix. |
| `tests/test_agent_authored_contract_flags.py` | Remove `retrieval_eligible` from required fields assertions. |
| `tests/test_retrieval_foundation.py` | Remove eligibility filter assertions. |
| `tests/test_dreamer_eval.py` | Remove `*_keys` from bead setup; use entities + associations as signal source. |
| `tests/test_dreamer_analysis.py` | Same as above. |

### Field removal (remove from bead construction + assertions)

- `tests/test_schema_models_serialization.py`
- `tests/test_schema_models_normalization.py`
- `tests/test_engine_invariants.py`
- `tests/test_agent_authored_runtime_gate.py`
- `tests/test_rationale_extraction.py`
- `tests/test_retrieval_semantic_backend.py` — Remove `retrieval_eligible=True` from test bead setup
- `tests/test_qdrant_embedded_backend.py` — Remove eligibility filter from queries
- `tests/test_neo4j_mapping_contract.py` — Remove `retrieval_eligible` column from schema mapping
- `tests/test_bead_projection.py` — Remove `retrieval_title` preference test; update `_LIST_FIELDS` assertion

### Benchmarks

- `benchmarks/locomo/ingest.py:80` — Remove `retrieval_eligible` from bead construction; remove `locomo_turn_crawler` extraction path (demo work)
- `benchmarks/locomo_like/runner.py:118` — Same

---

## Agent instruction gaps summary

| File | Gap | Fix |
|---|---|---|
| `policy/bead_judge.py` | Instructs `retrieval_eligible`, `retrieval_title`, `retrieval_facts` | Rewrite prompt: title=specific, summary≠title, entities=noun-phrases, `supporting_facts` replaces `retrieval_facts`, remove gate fields |
| `integrations/mcp/core-memory-agent-guide.md` | No guidance on entity quality or summary distinctness | Add: multi-word entities, summary must differ from title |
| `association/crawler_contract.py:196–230` | Contract doc lists dropped fields as accepted | Remove dropped fields, add `because`/`supporting_facts` as quality signals |
| `policy/hygiene.py` | `extract_entities()` is a caps-token regex | Replace with noun-phrase heuristic (Phase 3) |
| `runtime/engine.py` (observe mode) | Judge never runs when caller supplies `crawler_updates` | Run judge always; let caller augment, not replace (Phase 9) |
| `policy/hygiene.py:331–332` | `prev_bead_id` auto-link is a no-op stub | Implement in persistence write path (Phase 10) |
