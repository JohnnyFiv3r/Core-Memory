# LangChain Quickstart

## Install
```bash
pip install "core-memory[langchain]"
```

## CoreMemory (continuity + writeback)
```python
from core_memory.integrations.langchain import CoreMemory

memory = CoreMemory(root="./memory", session_id="lc-session-1")
```

## CoreMemoryRetriever (read-time recall)
```python
from core_memory.integrations.langchain import CoreMemoryRetriever

retriever = CoreMemoryRetriever(root="./memory", k=8, explain=True)
```

## Validate
```bash
python -m unittest tests.test_langchain_adapter_contract
```
