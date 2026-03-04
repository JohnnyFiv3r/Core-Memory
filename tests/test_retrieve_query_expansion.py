import tempfile
import unittest

from core_memory.store import MemoryStore


class TestRetrieveQueryExpansion(unittest.TestCase):
    def test_openclaw_to_multi_orchestrator_recall(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            store.add_bead(
                type="decision",
                title="OpenClaw-only to multi-orchestrator migration",
                summary=[
                    "Introduced emit_turn_finalized integration port",
                    "Added PydanticAI and SpringAI adapters",
                ],
                tags=["migration", "openclaw", "multi-orchestrator", "pydanticai", "springai"],
                session_id="main",
            )

            out = store.retrieve_with_context(
                query_text="remember when we switched from openclaw only to multiple orchestrators",
                limit=5,
                auto_memory_intent=False,
            )

            self.assertGreaterEqual(out.get("query_token_count", 0), 6)
            self.assertTrue(any("multi-orchestrator" in " ".join(r.get("summary", [])) or "multi-orchestrator" in (r.get("title") or "").lower() for r in out["results"]))


if __name__ == "__main__":
    unittest.main()
