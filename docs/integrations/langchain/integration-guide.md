# LangChain Integration Guide

## CoreMemory
`CoreMemory` implements a memory interface for continuity injection plus finalized-turn writeback.

Use it when you want conversation continuity and durable turn persistence with minimal wiring.

## CoreMemoryRetriever
`CoreMemoryRetriever` maps Core Memory search results into LangChain `Document` objects.

Current behavior:
- canonical anchor retrieval from Core Memory
- enrichment by bead id for stronger `page_content` (title + summary/detail when available)
- metadata includes bead id/type/status/score and source markers

## Practical split
- continuity/context support: `CoreMemory`
- retrieval support: `CoreMemoryRetriever`

Use both when your app needs continuity and explicit retriever-driven recall.
