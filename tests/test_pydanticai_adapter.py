import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

import core_memory.integrations.pydanticai.run as pyd_run
from core_memory.integrations.pydanticai import run_with_memory, run_with_memory_sync


class FakeResult:
    def __init__(self, output: str):
        self.output = output


class FakeAgent:
    async def run(self, user_query: str):
        return FakeResult(output=f"ok:{user_query}")


class FakeSyncAgent:
    def run_sync(self, user_query: str):
        return FakeResult(output=f"ok:{user_query}")


class TestPydanticAiAdapter(unittest.TestCase):
    def test_pydanticai_emits_one_event_per_run(self):
        async def _run():
            with tempfile.TemporaryDirectory() as td:
                root = str(Path(td) / "memory")
                agent = FakeAgent()
                await run_with_memory(agent, "hello", root=root, session_id="s1", turn_id="t1")
                events_file = Path(root) / ".beads" / "events" / "memory-events.jsonl"
                rows = [json.loads(l) for l in events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
                self.assertEqual(1, len(rows))

        asyncio.run(_run())

    def test_pydanticai_idempotency_same_turn_id(self):
        async def _run():
            with tempfile.TemporaryDirectory() as td:
                root = str(Path(td) / "memory")
                agent = FakeAgent()
                await run_with_memory(agent, "hello", root=root, session_id="s1", turn_id="t1")
                await run_with_memory(agent, "hello", root=root, session_id="s1", turn_id="t1")
                state_file = Path(root) / ".beads" / "events" / "memory-pass-state.json"
                state = json.loads(state_file.read_text(encoding="utf-8"))
                self.assertIn("s1:t1", state)

        asyncio.run(_run())

    def test_pydanticai_sync_wrapper_and_default_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            os.environ["CORE_MEMORY_ROOT"] = root
            try:
                agent = FakeSyncAgent()
                result = run_with_memory_sync(agent, "hello", session_id="s-sync", turn_id="t-sync")
                self.assertEqual("ok:hello", result.output)
                events_file = Path(root) / ".beads" / "events" / "memory-events.jsonl"
                rows = [json.loads(l) for l in events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
                self.assertEqual(1, len(rows))
                md = ((rows[0].get("envelope") or {}).get("metadata") or {})
                self.assertEqual("native", md.get("adapter_kind"))
                self.assertEqual("pydanticai", md.get("adapter_runtime"))
                self.assertTrue(md.get("fail_open"))
            finally:
                os.environ.pop("CORE_MEMORY_ROOT", None)

    def test_pydanticai_fail_open_on_emit_error(self):
        original = pyd_run.process_turn_finalized

        def _boom(*args, **kwargs):
            raise RuntimeError("emit failed")

        pyd_run.process_turn_finalized = _boom
        try:
            agent = FakeSyncAgent()
            result = run_with_memory_sync(agent, "hello", session_id="s-sync", turn_id="t-sync")
            self.assertEqual("ok:hello", result.output)
        finally:
            pyd_run.process_turn_finalized = original


if __name__ == "__main__":
    unittest.main()
