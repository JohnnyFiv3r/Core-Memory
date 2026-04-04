import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch

from core_memory.runtime.engine import process_flush


def _has_session_start_marker(root: str, session_id: str) -> bool:
    idx = Path(root) / ".beads" / "index.json"
    if not idx.exists():
        return False
    obj = json.loads(idx.read_text(encoding="utf-8"))
    for b in (obj.get("beads") or {}).values():
        if str((b or {}).get("session_id") or "") != session_id:
            continue
        tags = {str(t) for t in ((b or {}).get("tags") or [])}
        if "session_start" in tags:
            return True
    return False


class _DummySyncResult:
    def __init__(self, output: str):
        self.output = output


class _DummySyncAgent:
    def run_sync(self, prompt: str):
        return _DummySyncResult(f"assistant: {prompt}")


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


class TestAdapterParityMatrixContract(unittest.TestCase):
    def test_http_adapter_parity(self):
        try:
            from fastapi.testclient import TestClient
            from core_memory.integrations.http.server import app
        except Exception:
            self.skipTest("fastapi test stack unavailable")

        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"},
            clear=False,
        ):
            root = str(Path(td) / "memory")
            session_id = "http-s1"
            c = TestClient(app)

            # turn finalized boundary
            r1 = c.post(
                "/v1/memory/turn-finalized",
                json={
                    "root": root,
                    "session_id": session_id,
                    "turn_id": "t1",
                    "user_query": "why",
                    "assistant_final": "because",
                },
            )
            self.assertEqual(200, r1.status_code)

            # session start continuity boundary
            r2 = c.get(
                "/v1/memory/continuity",
                params={"root": root, "session_id": session_id, "max_items": 10},
            )
            self.assertEqual(200, r2.status_code)
            self.assertTrue(_has_session_start_marker(root, session_id))

            # session flush boundary
            r3 = c.post("/v1/memory/session-flush", json={"root": root, "session_id": session_id})
            self.assertEqual(200, r3.status_code)

            # retrieval family
            rs = c.post("/v1/memory/search", json={"root": root, "request": {"query_text": "why", "intent": "remember", "k": 5}})
            rt = c.post("/v1/memory/trace", json={"root": root, "query": "why", "k": 5})
            re = c.post("/v1/memory/execute", json={"root": root, "request": {"raw_query": "why", "intent": "causal", "k": 5}, "explain": True})
            self.assertIn(rs.status_code, (200, 503))
            self.assertIn(rt.status_code, (200, 503))
            self.assertIn(re.status_code, (200, 503))

    def test_openclaw_adapter_parity(self):
        from core_memory.integrations.openclaw_agent_end_bridge import process_agent_end_event
        from core_memory.integrations.openclaw_read_bridge import dispatch

        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"},
            clear=False,
        ):
            root = str(Path(td) / "memory")
            session_id = "oc-s1"

            # turn finalized boundary via agent_end bridge
            out = process_agent_end_event(
                root=root,
                event={
                    "messages": [
                        {"role": "user", "content": "question"},
                        {"role": "assistant", "content": "answer"},
                    ],
                },
                ctx={"sessionId": session_id, "runId": "r1"},
            )
            self.assertTrue(out.get("ok"))

            # session start + continuity
            c = dispatch({"action": "continuity", "root": root, "session_id": session_id, "max_items": 10})
            self.assertTrue(c.get("ok"))
            self.assertTrue(_has_session_start_marker(root, session_id))

            # session flush boundary via runtime helper (OpenClaw parity path)
            f = process_flush(
                root=root,
                session_id=session_id,
                source="openclaw_parity",
                promote=True,
                token_budget=1200,
                max_beads=12,
            )
            self.assertTrue(f.get("ok"))

            # retrieval family via read bridge
            s = dispatch({"action": "search", "root": root, "query": "question", "k": 5})
            t = dispatch({"action": "trace", "root": root, "query": "question", "k": 5})
            e = dispatch({"action": "execute", "root": root, "query": "question", "k": 5, "intent": "remember"})
            self.assertIn("ok", s)
            self.assertIn("ok", t)
            self.assertIn("ok", e)

    def test_pydanticai_adapter_parity(self):
        from core_memory.integrations.pydanticai import run_with_memory_sync, flush_session
        from core_memory.integrations.pydanticai.memory_tools import (
            continuity_prompt,
            memory_search_tool,
            memory_trace_tool,
            memory_execute_tool,
        )

        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"},
            clear=False,
        ):
            root = str(Path(td) / "memory")
            session_id = "pyd-s1"

            # turn finalized boundary
            run_with_memory_sync(_DummySyncAgent(), "why did we change", root=root, session_id=session_id)

            # session start + continuity
            prompt = continuity_prompt(root=root, session_id=session_id, max_items=10)
            self.assertIsInstance(prompt, str)
            self.assertTrue(_has_session_start_marker(root, session_id))

            # session flush boundary
            fo = flush_session(root=root, session_id=session_id)
            self.assertTrue(fo.get("ok"))

            # retrieval family
            s_tool = memory_search_tool(root=root)
            t_tool = memory_trace_tool(root=root)
            e_tool = memory_execute_tool(root=root)
            s = json.loads(s_tool("why", k=5))
            t = json.loads(t_tool("why", k=5))
            e = json.loads(e_tool("why", intent="remember"))
            self.assertIn("results", s)
            self.assertIn("ok", t)
            self.assertIn("ok", e)

    def test_langchain_adapter_parity(self):
        import importlib

        with tempfile.TemporaryDirectory() as td, _fake_langchain_core_modules(), patch.dict(
            os.environ,
            {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"},
            clear=False,
        ):
            root = str(Path(td) / "memory")
            session_id = "lc-s1"

            m = importlib.import_module("core_memory.integrations.langchain.memory")
            rmod = importlib.import_module("core_memory.integrations.langchain.retriever")
            CoreMemory = m.CoreMemory
            Retriever = rmod.CoreMemoryRetriever

            cm = CoreMemory(root=root, session_id=session_id)
            cm.save_context({"input": "why"}, {"output": "because"})
            _ = cm.load_memory_variables({})
            cm.clear()

            rr = Retriever(root=root, k=5, explain=True)
            out = rr._get_relevant_documents("why")
            self.assertTrue(len(out) >= 1)


if __name__ == "__main__":
    unittest.main()
