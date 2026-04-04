# LangChain Integration Guide

Status: Canonical

## Architecture fit
LangChain integration maps to the same Core Memory split used by other adapters:
- write ingestion (finalized-turn event path)
- runtime retrieval (search/trace/execute model in Core Memory)
- optional hydration if downstream flow needs raw source payloads

## Surface 1: `CoreMemory`
File: `core_memory/integrations/langchain/memory.py`

Role:
- implements LangChain `BaseMemory`
- loads continuity text into prompt memory variables
- saves context through canonical per-turn boundary (`process_turn_finalized`)
- treats `clear()` as session-end flush boundary (`process_flush`)

Practical behavior:
- continuity is prompt context support, not full retrieval planning
- writeback is append-only event ingest, not destructive memory mutation

## Surface 2: `CoreMemoryRetriever`
File: `core_memory/integrations/langchain/retriever.py`

Role:
- implements LangChain `BaseRetriever`
- executes Core Memory search and returns LangChain `Document` objects

Practical behavior:
- retrieval returns bead-oriented recall data with metadata
- retriever enriches anchors by bead id so `Document.page_content` includes summary/detail when available
- this is read-time recall, complementary to `CoreMemory` writeback/continuity

## Usage guidance
- Use `CoreMemory` when you want conversation memory continuity + automatic writeback.
- Use `CoreMemoryRetriever` when you want retriever-style document recall in RAG chains.
- Use both when you need continuity and explicit retriever recall in one app.

## Scope honesty
Current LangChain integration is practical and usable, but still lighter-weight than the most mature adapters. Treat it as a clean adapter surface, not a full framework rewrite.
