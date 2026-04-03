# LangChain Quickstart

Status: Canonical

## Goal
Use Core Memory with LangChain for both:
- conversation memory continuity (`CoreMemory`)
- retriever-based recall (`CoreMemoryRetriever`)

## 1) Install
```bash
pip install "core-memory[langchain]"
```

## 2) Conversation memory (writeback + continuity)
```python
from core_memory.integrations.langchain import CoreMemory

memory = CoreMemory(root="./memory", session_id="lc-session-1")
# pass into your chain/agent memory slot
```

`CoreMemory.save_context(...)` writes finalized turns into Core Memory.
`CoreMemory.load_memory_variables(...)` injects continuity context.

## 3) Retriever usage (read-time recall)
```python
from core_memory.integrations.langchain import CoreMemoryRetriever

retriever = CoreMemoryRetriever(root="./memory", k=8, explain=True)
# pass into your retrieval chain
```

## 4) Validate quickly
- Ensure turns are being written under `.beads/events/`
- Query through retriever and verify returned docs include bead metadata (`bead_id`, `type`, `score`)
