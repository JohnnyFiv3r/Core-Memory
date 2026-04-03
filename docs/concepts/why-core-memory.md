# Why Core Memory

Status: Canonical concept framing

## The short version
Core Memory exists because many agent systems can replay context, but struggle to preserve durable, structured reasoning over time.

It is built to answer:
- what happened,
- why it happened,
- and what evidence/turns support that explanation.

## Why beads
A bead is a typed durable memory object (decision, evidence, lesson, context, outcome, etc.).

Compared with raw transcript chunks, beads give you:
- stable memory units that survive compaction
- explicit semantics (`type`, status, tags, lineage)
- cleaner recall behavior under token constraints

## Why durable memory objects instead of only transcript chunks
Transcript chunks are useful for recency and verbatim phrasing.
They are weaker as a long-horizon memory substrate because they are noisy, repetitive, and often weakly structured.

Core Memory keeps transcript authority where it belongs, but stores durable memory objects for long-horizon retrieval.

## Why causal/temporal associations matter
Many important agent questions are causal:
- “why did we choose this?”
- “what led to this outcome?”
- “what changed?”

Explicit associations let retrieval move beyond similarity-only recall and produce chain-backed grounding.

## Why source-turn authority and hydration matter
Beads summarize and structure memory.
Source turns preserve full-fidelity evidence.

Hydration bridges them:
- select memory first (`search`/`trace`/`execute`)
- hydrate source turns/tools only when needed for provenance or detail

This keeps normal retrieval fast while preserving auditability.

## Biology-inspired vs engineering reality
There are biological metaphors in the project language (e.g., compaction, hierarchy), but implementation decisions are engineering-driven:
- deterministic contracts
- explicit data boundaries
- inspectable side effects
- replayable tests

## What Core Memory is not
- not just a vector store wrapper
- not transcript replay as primary architecture
- not mystical “agent consciousness” machinery
- not a promise of perfect memory; it is a disciplined memory operating layer with explicit contracts and limits
