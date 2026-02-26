# mem-beads — Agent Memory Skill

Persistent causal agent memory with lossless compaction.

## Overview

This skill manages structured memory beads — typed, linked, append-only records of meaningful agent actions. Beads form causal chains across sessions, compact over time, but the full archive is always preserved.

## CLI Location

`/home/node/.openclaw/workspace/tools/mem-beads/mem-beads`

Or set PATH: `export PATH="/home/node/.openclaw/workspace/tools/mem-beads:$PATH"`

## Architecture: How Beads Get Written

### Per-Turn (inline, silent)
The main agent writes beads inline after significant turns. This is silent — no user-visible output. One `exec` call to the CLI (~100ms). Do NOT narrate bead creation to the user.

### Pre-Compaction (OpenClaw memory flush)
OpenClaw has a built-in **pre-compaction memory flush** — a silent agentic turn injected before auto-compaction. We customize this prompt to also create a `session_end` summary bead and run compaction on the session's beads.

Configure via `agents.defaults.compaction.memoryFlush.prompt` in `openclaw.json`.

### Sub-Agent (archived)
Sub-agent turn prompt archived at `tools/mem-beads/archive/turn_prompt.py`. Requires gateway token fix before use. Not needed for MVP — inline mode is simpler and reliable.

## Per-Turn Bead Writing

### When to Write a Bead

**WRITE a bead when the turn contains:**
- A **goal** — user states intent, requests something non-trivial
- A **decision** — a choice was made between alternatives, with rationale
- A **tool_call** — significant external action (deploy, commit, config change, API call). Skip trivial reads/searches.
- A **evidence** — data, output, or finding that supports or contradicts something
- A **outcome** — a goal was achieved, failed, or changed
- A **lesson** — insight learned from experience ("X doesn't work because Y", "always do Z before W")
- A **precedent** — discovered pattern, rule, or convention that should guide future behavior
- A **context** — important background info (user preference, project constraint, environment detail)

**DO NOT write a bead when:**
- Turn is casual chat, greetings, acknowledgments
- Turn is a simple factual Q&A with no lasting value
- Turn is a continuation of an already-beaded action (don't double-count)
- Turn is a heartbeat or system check with no findings
- Bead would duplicate one already written this session

### Bead-Worthiness Decision Tree

```
1. Did something meaningful happen?
   NO → skip
   YES ↓
2. Will this matter in a future session?
   NO → skip
   YES ↓
3. Is it already captured by an existing bead?
   YES → skip (or link to it)
   NO ↓
4. Write the bead.
```

### How to Write (silently)

Call the CLI directly. **Do not narrate this to the user** — bead writing is background bookkeeping.

```bash
/home/node/.openclaw/workspace/tools/mem-beads/mem-beads create \
  --type <type> \
  --title "Short descriptive title" \
  --summary "Key point 1" "Key point 2" \
  --session <session-id> \
  --scope <personal|project|global> \
  --tags "tag1,tag2" \
  --confidence <0.0-1.0>
```

**Title**: 5-15 words, specific, searchable. Good: "Fly.io deployment needs 0.0.0.0 bind". Bad: "Fixed a thing".

**Summary**: 1-3 bullet points capturing the essential info. Each point should stand alone.

**Scope**:
- `personal` — user preference, personal workflow, environment detail
- `project` — specific to a project (tag with project name)
- `global` — cross-project insight, universal lesson

**Confidence**:
- `0.9+` — user confirmed, verified outcome
- `0.7-0.9` — agent inferred with good evidence
- `0.5-0.7` — tentative, needs confirmation
- `<0.5` — speculative, flag for review

**Tags**: lowercase, comma-separated. Include project name if scope=project.

### Linking

When a new bead relates to a previous one:

```bash
mem-beads link --from <new-bead-id> --to <old-bead-id> --type <link_type>
```

Link types: `caused_by`, `led_to`, `blocked_by`, `unblocks`, `supersedes`, `superseded_by`, `associated_with`

### Session ID

Use the current OpenClaw session key as session_id. If unknown, use a descriptive slug like `main-2026-02-26` or `telegram-daily`.

## Sub-Agent Mode (for automated per-turn capture)

Spawn a lightweight sub-agent after significant turns:

```
Task: Analyze this agent turn and decide if it warrants a memory bead.

TURN CONTEXT:
User: {user_message}
Agent: {agent_response}
Tools used: {tool_calls_summary}

RULES:
- Only create a bead if this turn has lasting value for future sessions
- Use mem-beads create CLI at /home/node/.openclaw/workspace/tools/mem-beads/mem-beads
- Session ID: {session_id}
- If no bead warranted, respond with just: NO_BEAD
- If bead created, respond with the bead ID and type

BEAD TYPES: goal, decision, tool_call, evidence, outcome, lesson, precedent, context
DO NOT create: session_start, session_end (handled by session lifecycle)
```

Model: `minimax-fast` (cheap, fast judgment)
Timeout: 30s

## Retrieving Beads (Context Packing)

### At Session Start

Load recent context:
```bash
# Recent beads from last few sessions
mem-beads query --status open --limit 20
mem-beads query --status promoted --limit 20

# Stats overview
mem-beads stats
```

### On-Demand Recall

When a topic comes up that might have prior beads:
```bash
# By tag
mem-beads query --tag "fly-io" --full

# By type
mem-beads query --type lesson --scope project --full

# Uncompact for deep context
mem-beads uncompact --id <bead-id> --radius 3 --follow-links
```

## Session Lifecycle

### Session Start
```bash
/home/node/.openclaw/workspace/tools/mem-beads/mem-beads create --type session_start --title "Session: <brief description>" --session <id>
```

### Session End / Pre-Compaction
When the pre-compaction memory flush fires, run consolidation AND association analysis:
```bash
# 1. Consolidate session (promote, compact, rolling window)
python3 /home/node/.openclaw/workspace/tools/mem-beads/consolidate.py consolidate --session <id> --promote

# 2. Generate association analysis prompt
python3 /home/node/.openclaw/workspace/tools/mem-beads/associate.py prompt

# 3. Analyze beads and identify associations (inline, using main agent model)

# 4. Record any new associations
python3 /home/node/.openclaw/workspace/tools/mem-beads/associate.py record --associations '<json>'

# 5. Optionally surface interesting finds
python3 /home/node/.openclaw/workspace/tools/mem-beads/associate.py surface
```

This will:
1. Identify and auto-promote qualifying beads (lessons, decisions, precedents, outcomes at confidence >= 0.8)
2. Compact non-promoted beads
3. Regenerate `promoted-context.md`
4. Inject the rolling window into MEMORY.md (between HTML comment markers)
5. Analyze beads for semantic associations (cross-session patterns)
6. Surface interesting connections to the user (optional)

### Context Injection
The rolling window is injected into MEMORY.md (which OpenClaw auto-loads at session start).
It lives between `<!-- mem-beads:rolling-window:start -->` and `<!-- mem-beads:rolling-window:end -->` markers.
Consolidation replaces this section each time — no manual editing needed.

## Association Crawler

The association crawler identifies semantic relationships between beads across sessions and projects.

### Manual Run
```bash
# Generate analysis prompt
python3 /home/node/.openclaw/workspace/tools/mem-beads/associate.py prompt

# Record associations (from LLM analysis)
python3 /home/node/.openclaw/workspace/tools/mem-beads/associate.py record --associations '<json>'

# List existing associations
python3 /home/node/.openclaw/workspace/tools/mem-beads/associate.py list

# Surface interesting connections
python3 /home/node/.openclaw/workspace/tools/mem-beads/associate.py surface
```

### Trigger: Pre-Compaction Memory Flush
The association crawler runs during the pre-compaction memory flush — no cron needed. This fires automatically before OpenClaw compacts the session, keeping association discovery incremental and tied to session lifecycle.

### Alternative: Sub-Agent Mode (configurable)
For separate model control, spawn a sub-agent with a configurable model:

```bash
# Set model via env var before running
MEMBEADS_ASSOCIATION_MODEL=minimax/MiniMax-M2.5 ./run_association.sh
```

Or spawn manually:
```bash
sessions_spawn --task "<analysis task>" --model "minimax/MiniMax-M2.5" --label "association-crawl"
```

### Relationship Types
- `similar_pattern` — Same approach used in different contexts
- `same_mistake` — Same error repeated
- `transferable_lesson` — Insight applicable across domains
- `contradicts` — Two beads that conflict
- `reinforces` — Two beads that support each other
- `generalizes` — One is a generalization of another
- `specializes` — One is a specific case of another

## Promotion

Any bead type except `session_start` and `session_end` can be promoted:
```bash
mem-beads close --id <bead-id> --status promoted
```

"Promoted" = flagged as elevated importance. The session-end agent decides what to promote based on:
- Recall frequency (myelination)
- Confidence level
- Scope (global > project > personal for promotion priority)
- User confirmation boosts promotion likelihood
