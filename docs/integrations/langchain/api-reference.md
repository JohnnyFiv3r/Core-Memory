# LangChain API Reference

Status: Canonical

## Module exports
From `core_memory.integrations.langchain`:
- `CoreMemory`
- `CoreMemoryRetriever`

## `CoreMemory`
Implementation: `core_memory.integrations.langchain.memory.CoreMemory`

Key fields:
- `root`
- `session_id`
- `memory_key`
- `input_key`
- `output_key`
- `max_items`

Key methods:
- `load_memory_variables(inputs)`
- `save_context(inputs, outputs)`
- `clear()` (session-end flush boundary)

Behavior summary:
- load: explicit session-start boundary (`process_session_start`) then continuity injection text
- save: canonical per-turn write boundary
- clear: canonical session-end flush boundary

## `CoreMemoryRetriever`
Implementation: `core_memory.integrations.langchain.retriever.CoreMemoryRetriever`

Key fields:
- `root`
- `k`
- `explain`

Key method:
- `_get_relevant_documents(query, run_manager=None)`

Behavior summary:
- calls canonical Core Memory search
- enriches anchors by bead id for stronger document content
- maps to LangChain `Document`
- includes bead metadata in `Document.metadata`

## Install requirement
LangChain adapter requires `langchain-core`:
```bash
pip install "core-memory[langchain]"
```
