# Mem.beads — Project Plan
## Persistent Causal Agent Memory with Compaction

**Author**: Johnny Inniger + Krusty
**Date**: 2026-02-26
**Status**: Concept → Architecture
**Vision**: A universal structured memory system for AI agents that preserves causal chains, compacts intelligently, and scales across sessions, projects, and deployments.

---

## 1. Problem Statement

Current agent memory is flat and fragile:
- **MEMORY.md** is a single curated file — one bad edit and context is lost
- **Daily notes** are raw logs — no structure, no relationships, no lifecycle
- **Context windows** are session-scoped — everything before this session is gone unless manually summarized
- **No causal links** — you can't trace why a decision was made or what evidence led to an outcome
- **No compaction** — memory either stays at full fidelity (too expensive) or gets summarized once (lossy)
- **No recovery** — compressed/summarized memories can't be expanded back to full detail

The result: agents forget, repeat mistakes, lose nuance, and can't explain their reasoning chain.

## 2. Core Concept

Every meaningful agent action becomes a **bead** on a causal chain. Beads are structured, typed, append-only, and linked. The system compacts over time but the full archive is always preserved — you can "zoom in" on any bead by uncompressing its neighbors.

### The Necklace Metaphor
Think of a conversation as a necklace. Each bead is a discrete unit of meaning — a goal, a decision, a tool call, evidence, an outcome, a lesson. The string connecting them is the causal chain. Over time, you can remove beads from the necklace (compact) but they stay in the jewelry box (archive). You can always restring them.

## 3. Memory Architecture — Four Layers

```
┌─────────────────────────────────────────────────┐
│  Layer 3: Long-Term Memory (MEMORY.md)          │
│  Promoted beads only. Curated. Months/years.    │
│  ~2-3k tokens                                   │
├─────────────────────────────────────────────────┤
│  Layer 2: Rolling Window (promoted-context.md)  │
│  Last ~10 sessions of medium-fidelity beads.    │
│  session_end summaries + promoted candidates.   │
│  ~3-5k tokens                                   │
├─────────────────────────────────────────────────┤
│  Layer 1: In-Session (full chat history)        │
│  Untouched. Per-turn beads written alongside.   │
│  session-{id}.jsonl linked to chat.             │
├─────────────────────────────────────────────────┤
│  Layer 0: Archive (.beads/*.jsonl)              │
│  Everything, forever, full fidelity.            │
│  Compressed beads are pointers, not deletions.  │
│  Lossless. Always recoverable.                  │
└─────────────────────────────────────────────────┘
```

### Token Budget (steady state)
| Layer | Tokens | Loaded when |
|-------|--------|-------------|
| Layer 3 (MEMORY.md) | ~2-3k | Every session start |
| Layer 2 (rolling window) | ~3-5k | Every session start |
| Layer 1 (chat history) | managed by platform | During session |
| Layer 0 (archive) | 0 (on-demand) | Tool call / uncompact |
| **Total memory overhead** | **~5-8k** | |

This leaves 190k+ tokens for actual conversation on a 200k context model.

## 4. Bead Schema

### Bead Types
| Type | Purpose | Promotion eligible? |
|------|---------|-------------------|
| `session_start` | Marks session boundary, loads context | No |
| `session_end` | Full session summary, triggers compaction | Always persists in rolling window |
| `goal` | User or agent intent | Yes, if recurring |
| `decision` | Choice made, with rationale | Yes |
| `tool_call` | External action taken | Rarely |
| `evidence` | Data/output supporting a decision | Yes, if referenceable |
| `outcome` | Result of a goal/decision chain | Yes |
| `lesson` | Insight derived from outcome | Yes (primary promotion target) |
| `checkpoint` | Intermediate state snapshot | No |
| `precedent` | Historical pattern/rule discovered | Yes |
| `association` | Cross-bead semantic link (Layer 3 enrichment) | N/A (meta-bead) |

### Bead Object (canonical schema)
```json
{
  "id": "bead-{ulid}",
  "type": "lesson",
  "created_at": "2026-02-26T17:30:00Z",
  "session_id": "chat-9f32",
  "turn_refs": ["turn_18", "turn_19"],
  
  "title": "Avoid SOP v2 for fryer model ABC",
  "summary": [
    "SOP v2 omits allergen purge step",
    "Caused QA rejection in two stores",
    "SOP v3 is required for model ABC"
  ],
  "detail": "...(full narrative, preserved in archive)...",
  
  "scope": "project | global | personal",
  "authority": "agent_inferred | user_confirmed | system",
  "confidence": 0.85,
  
  "links": {
    "caused_by": ["bead-{id}"],
    "led_to": ["bead-{id}"],
    "blocked_by": ["bead-{id}"],
    "unblocks": ["bead-{id}"],
    "supersedes": ["bead-{id}"],
    "superseded_by": null,
    "associated_with": ["bead-{id}"]
  },
  
  "evidence_refs": [
    {"doc_id": "SOP-N42", "section": "Allergen Safety"},
    {"tool_output_id": "qm_report_2026_07"}
  ],
  
  "tags": ["safety", "sop", "fryer"],
  
  "status": "open | closed | promoted | compacted | superseded",
  "promoted_at": null,
  "compacted_at": null,
  "compacted_form": null
}
```

### Compacted Bead (minimum viable)
```json
{
  "id": "bead-{ulid}",
  "type": "lesson",
  "title": "Avoid SOP v2 for fryer model ABC",
  "status": "compacted",
  "compacted_at": "2026-02-26T18:00:00Z",
  "links": { "superseded_by": "bead-{promoted-id}" }
}
```

~30-40 tokens vs ~200-300 for the full bead. 10x compression.

## 5. Sub-Agent Architecture

### Memory Sub-Agent (per-turn)
```
Trigger: end of each agent turn
Model: minimax-fast (cheap, fast)
Task:
  1. Receive last turn context (user msg + agent response + tool calls)
  2. Classify: does this turn warrant a bead? (not every turn does)
  3. If yes: create structured bead, append to session-{id}.jsonl
  4. Return immediately
Token cost: ~500-1000 per invocation
```

### Session-End Sub-Agent
```
Trigger: session end (explicit or timeout)
Model: minimax or claude-sonnet (needs judgment)
Task:
  1. Read full session chat + session beads
  2. Write session_end summary bead (200-300 tokens, full fidelity)
  3. Scan rolling window (last 10 session_end beads)
  4. Identify promotion candidates (lessons, decisions, precedents)
  5. Promote qualifying beads → append to MEMORY.md
  6. Run compaction script on non-promoted beads
  7. Regenerate promoted-context.md for next session
  8. Write compaction stats to daily notes
```

### Association Crawler (scheduled)
```
Trigger: cron (daily or every few days)
Model: minimax or claude-sonnet
Task:
  1. Read Layer 3 (MEMORY.md promoted beads)
  2. Read recent Layer 2 (rolling window)
  3. Identify semantic associations between beads:
     - Similar topics across different projects
     - Patterns that repeat (mistakes, strategies, preferences)
     - Cross-project lessons (Line Lead safety ↔ Delta Bravo compliance)
  4. Create `association` meta-beads linking related beads
  5. Optionally surface interesting connections to user
     ("I noticed the allergen handling pattern from Line Lead 
      is similar to the compliance workflow in Delta Bravo")
```

This is the "low-tech graph vector DB" — semantic links built by an agent reading its own memory, not by embedding math. More explainable, auditable, and the agent can articulate *why* two things are related.

## 6. Daemon / Writer Interface

The sub-agent interacts with beads through a minimal script/tool:

### Commands
```bash
# Write a bead
mem-beads create --type lesson --session chat-9f32 --payload '{...}'

# Link beads
mem-beads link --from bead-abc --to bead-xyz --type caused_by

# Close/finalize a bead
mem-beads close --id bead-abc --status promoted

# Query beads
mem-beads query --type lesson --scope project --status open --limit 20
mem-beads query --session chat-9f32
mem-beads query --linked-to bead-abc

# Compact
mem-beads compact --before 2026-02-20 --keep-promoted
mem-beads compact --session chat-old123

# Uncompact (restore full detail from archive)
mem-beads uncompact --id bead-abc
mem-beads uncompact --around bead-abc --radius 5
```

### Implementation
- **v1**: Shell script + `jq` operating on JSONL files
- **v2**: Node.js daemon with file locking, in-memory index
- **v3**: Optional SQLite backend for complex queries

The writer ensures:
- ULID-based ID assignment (sortable, unique)
- Append-only writes (never mutate, only append new state)
- Schema validation before write
- Link integrity (referenced beads must exist)
- Concurrency safety (file locks or single-writer pattern)
- Policy enforcement (configurable rules like "global scope requires user_confirmed authority")

## 7. Uncompaction — The Killer Feature

Unlike traditional summarization which is lossy, mem.beads compaction is **lossless**. The full JSONL archive is always preserved. Uncompaction means:

1. Agent encounters a promoted bead that needs more context
2. Calls `mem-beads uncompact --around bead-abc --radius 5`
3. System reads the archive, finds the full bead + its 5 neighbors on the causal chain
4. Returns full-fidelity content for those beads
5. Agent now has detailed context without having loaded it at session start

**Use cases:**
- "Why did we decide X?" → uncompact the decision bead + its evidence chain
- "What happened in that session where we broke the build?" → uncompact session_end bead + surrounding beads
- Debugging a recurring issue → uncompact all beads tagged with the topic
- Onboarding a new project context → uncompact all promoted beads for that project scope

This is fundamentally different from RAG. RAG retrieves similar chunks. Uncompaction retrieves **causally linked structured memories** — the chain of reasoning, not just relevant text.

## 8. Association Layer — Low-Tech Graph

The scheduled association crawler builds a semantic graph over time:

```
[Line Lead: allergen safety lesson]
    ├── associated_with → [Delta Bravo: compliance check pattern]
    ├── associated_with → [Personal: "always verify safety-critical SOPs"]
    └── associated_with → [Precedent: FDA audit response template]
```

### Association Bead Schema
```json
{
  "id": "assoc-{ulid}",
  "type": "association",
  "source_bead": "bead-abc",
  "target_bead": "bead-xyz",
  "relationship": "similar_pattern | same_mistake | transferable_lesson | contradicts",
  "explanation": "Both involve safety-critical step validation in regulated workflows",
  "confidence": 0.8,
  "discovered_at": "2026-02-26T20:00:00Z"
}
```

### Why not just use embeddings?
- Associations are **explainable** — the agent writes *why* two things are related
- They're **auditable** — the user can review and correct them
- They're **causal** — they know the direction of the relationship
- They **persist** as first-class beads — they compact, promote, and link like anything else
- They get **better over time** as the agent accumulates more context
- No vector DB infrastructure needed

## 9. Cross-Platform Vision

Mem.beads is agent-framework agnostic. The core is:
- A JSONL file format
- A CLI tool for read/write/query/compact
- A sub-agent pattern (write per turn, compact per session, associate on schedule)

This works with:
- **OpenClaw** (current target) — sub-agents via sessions_spawn
- **LangChain/LangGraph** — as a tool/memory provider
- **Spring AI** (Line Lead) — as a Java service wrapping the CLI
- **Any agent framework** — the JSONL format is the interface

### Potential distribution
- **OpenClaw skill** — drop-in memory upgrade for any OpenClaw agent
- **npm package** — `mem-beads` CLI + Node.js library
- **Python package** — for LangChain/CrewAI/AutoGen ecosystems
- **Standalone spec** — just the schema + format, implement in any language

## 10. Comparison to Existing Approaches

| Approach | Structured? | Causal? | Compactable? | Recoverable? | Cross-session? |
|----------|------------|---------|-------------|-------------|---------------|
| Flat MEMORY.md | ❌ | ❌ | ❌ (manual) | ❌ | ✅ |
| Daily notes | ❌ | ❌ | ❌ | ✅ (raw logs) | ❌ |
| RAG / vector DB | ❌ | ❌ | N/A | ✅ | ✅ |
| Conversation summarization | ❌ | ❌ | ✅ (lossy) | ❌ | ✅ |
| **Mem.beads** | ✅ | ✅ | ✅ (lossless) | ✅ | ✅ |

## 11. Implementation Phases

### Phase 1: Foundation (MVP)
**Goal**: Basic bead creation, storage, and session-end compaction working in OpenClaw.

- [ ] Define bead JSON schema (types, required fields, link types)
- [ ] Build `mem-beads` CLI (create, query, compact, uncompact)
- [ ] Build per-turn memory sub-agent prompt
- [ ] Build session-end sub-agent prompt
- [ ] Wire into OpenClaw AGENTS.md workflow
- [ ] Storage: flat JSONL in `.beads/` directory
- [ ] Index: simple JSON index file updated on write
- [ ] Test with real conversations (dogfood on this OpenClaw instance)

**Deliverables**: Working skill, CLI, sub-agent prompts
**Estimate**: 3-5 days

### Phase 2: Rolling Window + Promotion
**Goal**: Multi-session memory with intelligent promotion to long-term.

- [ ] Session_end summary beads with full-fidelity capture
- [ ] Rolling window: last 10 sessions in promoted-context.md
- [ ] Promotion pipeline: sub-agent identifies candidates, writes to MEMORY.md
- [ ] Compaction script: compress non-promoted beads to minimum viable
- [ ] Uncompact tool: restore full detail from archive on demand
- [ ] Context injection: auto-load rolling window + MEMORY.md at session start

**Deliverables**: Full memory lifecycle working
**Estimate**: 2-3 days

### Phase 3: Association Crawler
**Goal**: Semantic graph layer built by scheduled sub-agent.

- [ ] Association bead type + schema
- [ ] Scheduled cron job for association discovery
- [ ] Cross-project pattern detection
- [ ] Surface interesting connections to user
- [ ] Association-aware query (find beads related to X via associations)

**Deliverables**: Low-tech graph memory
**Estimate**: 2-3 days

### Phase 4: Packaging + Distribution
**Goal**: Make mem.beads usable by anyone.

- [ ] OpenClaw skill package (drop-in)
- [ ] npm package with CLI
- [ ] Documentation + examples
- [ ] Schema spec (versioned, framework-agnostic)
- [ ] Optional: Python package
- [ ] Optional: SQLite backend for large archives
- [ ] Optional: Web UI for browsing bead chains

**Deliverables**: Published packages
**Estimate**: 3-5 days

## 12. Open Questions

1. **Bead granularity**: Not every turn deserves a bead. What's the threshold? The per-turn sub-agent needs clear criteria for "this is bead-worthy."

2. **Promotion authority**: Should all promotions be auto, or should high-scope beads (global/cross-project) require user confirmation? Configurable policy.

3. **Association quality**: Will a cheap model (minimax-fast) produce good associations, or does this need a more capable model? Probably needs testing.

4. **Storage limits**: JSONL files grow forever. At what point do we need archival (zip old files, move to cold storage)? Probably not a concern until thousands of sessions.

5. **Multi-agent**: If multiple agents share a `.beads/` directory, how do we handle concurrent writes? File locking? Separate files per agent with a merge step?

6. **Privacy**: Beads contain conversation summaries. How do we handle PII? Scope-based access controls? Encryption at rest?

7. **Schema evolution**: How do we handle adding new bead types or fields without breaking existing archives? Versioned schema with migration support.

8. **Cost**: Per-turn sub-agent on minimax-fast is ~$0.001-0.005 per turn. Session-end on a better model ~$0.01-0.05. Association crawler daily ~$0.05-0.10. Total: maybe $0.50-1.00/day for an active agent. Acceptable?

## 13. Why This Matters Beyond Line Lead

- **Personal AI assistants** (like this OpenClaw instance) — memory that actually works across months of conversations
- **Enterprise agents** — auditable decision chains, compliance-friendly
- **Multi-agent systems** — shared structured memory between specialized agents
- **AI coding assistants** — track decisions, refactors, architectural choices across projects
- **Customer support bots** — remember customer context without re-asking
- **Research agents** — build knowledge graphs from investigation chains

The insight is that **memory is not a blob of text — it's a graph of structured, typed, causally-linked events**. Mem.beads is the simplest possible implementation of that insight.

---

*"The palest ink is better than the best memory." — Chinese proverb*
*"But structured ink with causal links is better than pale ink." — Mem.beads*
