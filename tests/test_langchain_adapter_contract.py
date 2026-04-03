import importlib
import json
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path

from core_memory.persistence.rolling_record_store import write_rolling_records
from core_memory.persistence.store import MemoryStore


@contextmanager
def _fake_langchain_core_modules():
    names = [
        "langchain_core",
        "langchain_core.memory",
        "langchain_core.callbacks",
        "langchain_core.documents",
        "langchain_core.retrievers",
        "core_memory.integrations.langchain",
        "core_memory.integrations.langchain.memory",
        "core_memory.integrations.langchain.retriever",
    ]
    saved = {n: sys.modules.get(n) for n in names}
    try:
        lc = types.ModuleType("langchain_core")
        lc.__path__ = []

        mem = types.ModuleType("langchain_core.memory")

        class BaseMemory:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        mem.BaseMemory = BaseMemory

        cb = types.ModuleType("langchain_core.callbacks")

        class CallbackManagerForRetrieverRun:
            pass

        cb.CallbackManagerForRetrieverRun = CallbackManagerForRetrieverRun

        docs = types.ModuleType("langchain_core.documents")

        class Document:
            def __init__(self, page_content: str, metadata: dict | None = None):
                self.page_content = page_content
                self.metadata = dict(metadata or {})

        docs.Document = Document

        ret = types.ModuleType("langchain_core.retrievers")

        class BaseRetriever:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        ret.BaseRetriever = BaseRetriever

        sys.modules["langchain_core"] = lc
        sys.modules["langchain_core.memory"] = mem
        sys.modules["langchain_core.callbacks"] = cb
        sys.modules["langchain_core.documents"] = docs
        sys.modules["langchain_core.retrievers"] = ret

        # Ensure Core Memory langchain modules are re-imported against fakes.
        for n in [
            "core_memory.integrations.langchain",
            "core_memory.integrations.langchain.memory",
            "core_memory.integrations.langchain.retriever",
        ]:
            sys.modules.pop(n, None)

        yield
    finally:
        for n, mod in saved.items():
            if mod is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = mod


class TestLangChainAdapterContract(unittest.TestCase):
    def test_corememory_load_memory_variables_returns_continuity_text(self):
        with tempfile.TemporaryDirectory() as td, _fake_langchain_core_modules():
            root = Path(td) / "memory"
            root.mkdir(parents=True, exist_ok=True)
            write_rolling_records(
                str(root),
                records=[{"type": "decision", "title": "Use canonical execute", "summary": ["single runtime path"]}],
                meta={},
                included_bead_ids=[],
                excluded_bead_ids=[],
            )

            m = importlib.import_module("core_memory.integrations.langchain.memory")
            CoreMemory = m.CoreMemory
            cm = CoreMemory(root=str(root), session_id="lc-s1", memory_key="memory")
            out = cm.load_memory_variables({})

            self.assertIn("memory", out)
            self.assertIn("Use canonical execute", out["memory"])

    def test_corememory_save_context_emits_turn_event(self):
        with tempfile.TemporaryDirectory() as td, _fake_langchain_core_modules():
            root = Path(td) / "memory"
            m = importlib.import_module("core_memory.integrations.langchain.memory")
            CoreMemory = m.CoreMemory
            cm = CoreMemory(root=str(root), session_id="lc-s2", input_key="input", output_key="output")

            cm.save_context({"input": "why did we change?"}, {"output": "because X"})

            events = root / ".beads" / "events" / "memory-events.jsonl"
            self.assertTrue(events.exists())
            rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(1, len(rows))

    def test_corememory_retriever_returns_documents(self):
        with tempfile.TemporaryDirectory() as td, _fake_langchain_core_modules():
            root = Path(td) / "memory"
            s = MemoryStore(str(root))
            s.add_bead(
                type="decision",
                title="Candidate-first promotion",
                summary=["promotion workflow"],
                tags=["promotion_workflow"],
                session_id="main",
                source_turn_ids=["t1"],
            )

            rmod = importlib.import_module("core_memory.integrations.langchain.retriever")
            Retriever = rmod.CoreMemoryRetriever
            retriever = Retriever(root=str(root), k=5, explain=True)

            docs = retriever._get_relevant_documents("candidate promotion")
            self.assertTrue(len(docs) >= 1)
            first = docs[0]
            self.assertTrue(hasattr(first, "page_content"))
            self.assertTrue(hasattr(first, "metadata"))
            self.assertIn("bead_id", first.metadata)
            self.assertIn("source", first.metadata)


if __name__ == "__main__":
    unittest.main()
