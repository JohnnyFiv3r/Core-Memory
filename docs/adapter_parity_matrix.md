# Adapter Parity Matrix

Status: Canonical parity reference

This matrix tracks whether each first-class adapter honors the same lifecycle and retrieval contract.

Legend:
- ✅ implemented as adapter-facing behavior
- ⚠️ available via core runtime helper, not direct adapter API

| Adapter | Session start | Turn finalized | Session flush | Continuity load | Search | Trace | Execute |
|---|---:|---:|---:|---:|---:|---:|---:|
| HTTP | ✅ (`/v1/memory/continuity?session_id=...`) | ✅ (`/v1/memory/turn-finalized`) | ✅ (`/v1/memory/session-flush`) | ✅ | ✅ | ✅ | ✅ |
| OpenClaw | ✅ (`read-bridge continuity` with `session_id`) | ✅ (`openclaw_agent_end_bridge`) | ⚠️ (`process_flush` runtime boundary) | ✅ | ✅ | ✅ | ✅ |
| PydanticAI | ✅ (`continuity_prompt(..., session_id=...)`) | ✅ (`run_with_memory*`) | ✅ (`flush_session`) | ✅ | ✅ | ✅ | ✅ |
| LangChain | ✅ (`CoreMemory.load_memory_variables`) | ✅ (`CoreMemory.save_context`) | ✅ (`CoreMemory.clear`) | ✅ | ✅ (`CoreMemoryRetriever`) | ✅ (via canonical trace surface) | ✅ (via canonical execute surface) |

## Notes
- All adapters should preserve canonical retrieval semantics (`search` / `trace` / `execute`) and explicit hydration boundaries.
- Friendly adapter aliases are allowed only when they map cleanly to canonical facets and are documented as aliases.
