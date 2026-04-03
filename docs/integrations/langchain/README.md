# LangChain Integration

Status: Canonical adapter surface

Core Memory exposes two LangChain-facing primitives:

1. `CoreMemory`
   - conversation memory / continuity injection
   - finalized-turn writeback

2. `CoreMemoryRetriever`
   - read-time recall retriever
   - returns enriched bead documents (not raw thin anchors)

See:
- `quickstart.md`
- `integration-guide.md`
- `api-reference.md`
