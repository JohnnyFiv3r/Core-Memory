# LangChain Integration Docs

Status: Canonical adapter docs

## Start here
- `quickstart.md`
- `integration-guide.md`
- `api-reference.md`

## Integration surfaces
LangChain is documented as two related but distinct surfaces:

1. **CoreMemory**
   - conversation memory / continuity injection
   - finalized-turn writeback into Core Memory

2. **CoreMemoryRetriever**
   - retriever adapter for read-time recall
   - returns enriched bead documents (not just thin anchor labels)

This is not write-only. LangChain integration supports both writeback and retrieval.

## Related canonical docs
- `../../canonical_surfaces.md`
- `../../core_adapters_architecture.md`
- `../../integration_contract.md`
