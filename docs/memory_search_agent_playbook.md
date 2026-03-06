# Memory Search Agent Playbook (Tool-First Boundary)

Status: Supporting
Canonical surfaces: agent-side usage guidance for `memory.search` / `memory.execute`
See also:
- `docs/index.md`
- `docs/canonical_surfaces.md`
- `docs/memory_search_skill.md`

Use this in orchestrators/prompts so the agent consistently uses the typed memory-search tool.

## Required policy

If user asks about prior work/memory (remember/recall/why/when/what changed), the agent SHOULD:
1. Call `core.memory_search.get_search_form`
2. Fill form from user intent
3. Call `core.memory_search.search_typed`
4. Answer with citations from returned `results` (and chains if present)

Do not answer from vague internal memory when this tool is available.

## Suggested prompt snippet

```text
When the user asks about prior work, decisions, causes, timelines, or "remember X":
- First call core.memory_search.get_search_form.
- Fill the returned form fields conservatively.
- Then call core.memory_search.search_typed.
- Base your answer on returned results/chains only.
- If confidence is low or suggested_next is ask_clarifying, ask one focused clarification.
```

## Form-fill heuristics (minimal)

- intent:
  - why/caused/rationale -> causal
  - what changed/updated/replaced -> what_changed
  - when/date/timeline -> when
  - remember/recall/remind me -> remember
- require_structural:
  - **agent-chosen**, default false
  - set true only when user explicitly asks for causal/evidence chain grounding
- must_terms:
  - include 1-3 high-signal nouns from user text
- avoid_terms:
  - optional; only if user explicitly excludes a topic
- topic_keys/incident_id:
  - provide guesses if obvious; tool will snap or reject safely

## Answer policy

- Include 2-5 concise citations: bead_id + title
- If asked "why", include chain-backed explanation when available
- If tool warns `no_strong_anchor_match_free_text_mode`, mention uncertainty briefly

## Failure handling

- no results + suggested_next=ask_clarifying -> ask one disambiguation question
- low/medium confidence + broad query -> run one broadened retry (higher k or fewer filters)
- still low -> return best-effort summary with explicit uncertainty
