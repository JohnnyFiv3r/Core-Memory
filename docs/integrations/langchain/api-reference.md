# LangChain API Reference

## Exports
From `core_memory.integrations.langchain`:
- `CoreMemory`
- `CoreMemoryRetriever`

## CoreMemory
File: `core_memory/integrations/langchain/memory.py`

Primary methods:
- `load_memory_variables(inputs)`
- `save_context(inputs, outputs)`
- `clear()`

## CoreMemoryRetriever
File: `core_memory/integrations/langchain/retriever.py`

Primary method:
- `_get_relevant_documents(query, run_manager=None)`

Returns LangChain `Document` objects with enriched bead content and metadata.
