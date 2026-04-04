# Adapter Parity Matrix

Status: Canonical parity reference

This matrix tracks whether each first-class adapter honors the same lifecycle and retrieval contract.

Legend:
- ✅ implemented as adapter-facing behavior
- ⚠️ available via core runtime helper, not direct adapter API

| Adapter | Session start | Turn finalized | Session flush | Continuity load | Search | Trace | Execute |
|---|---:|---:|---:|---:|---:|---:|---:|
| HTTP | ✅ (`POST /v1/memory/session-start`) | ✅ (`/v1/memory/turn-finalized`) | ✅ (`/v1/memory/session-flush`) | ✅ (`GET /v1/memory/continuity`) | ✅ | ✅ | ✅ |
| OpenClaw | ✅ (`read-bridge action=session_start`) | ✅ (`openclaw_agent_end_bridge`) | ⚠️ (`process_flush` runtime boundary) | ✅ (`read-bridge action=continuity`) | ✅ | ✅ | ✅ |
| PydanticAI | ✅ (`ensure_session_start(...)`) | ✅ (`run_with_memory*`) | ✅ (`flush_session`) | ✅ (`continuity_prompt(...)`) | ✅ | ✅ | ✅ |
| LangChain | ✅ (`CoreMemory.load_memory_variables` calls `process_session_start`) | ✅ (`CoreMemory.save_context`) | ✅ (`CoreMemory.clear`) | ✅ (`load_memory_variables` continuity read) | ✅ (`CoreMemoryRetriever`) | ✅ (via canonical trace surface) | ✅ (via canonical execute surface) |

## Notes
- All adapters should preserve canonical retrieval semantics (`search` / `trace` / `execute`) and explicit hydration boundaries.
- Friendly adapter aliases are allowed only when they map cleanly to canonical facets and are documented as aliases.
- Continuity load is a pure read surface. It must not create `session_start` or mutate semantic dirty state.
