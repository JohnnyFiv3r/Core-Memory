import tempfile
import unittest
from unittest.mock import patch

from core_memory.runtime.engine import process_turn_finalized, process_flush
from core_memory.runtime.worker import SidecarPolicy
from core_memory.persistence.store import MemoryStore


class TestMemoryEngine(unittest.TestCase):
    def test_process_turn_finalized(self):
        with tempfile.TemporaryDirectory() as td:
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="remember this",
                assistant_final="Decision: use memory engine entrypoint",
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("canonical_in_process", out.get("authority_path"))
            self.assertTrue((out.get("engine") or {}).get("normalized"))
            self.assertTrue((out.get("crawler_handoff") or {}).get("required"))

    def test_process_flush(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="x", summary=["y"], session_id="main", source_turn_ids=["t1"])
            out = process_flush(
                root=td,
                session_id="main",
                promote=False,
                token_budget=300,
                max_beads=20,
                source="admin_cli",
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("canonical_in_process", out.get("authority_path"))
            self.assertEqual("process_flush", (out.get("engine") or {}).get("entry"))

    def test_claim_layer_persists_memory_outcome_on_turn_bead(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_CLAIM_LAYER": "1",
                "CORE_MEMORY_CLAIM_EXTRACTION_MODE": "off",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t-mem-outcome",
                user_query="what did we decide?",
                assistant_final="you decided to use the memory engine",
                metadata={"used_memory": True, "retrieved_beads": [{"id": "b-prev"}]},
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("memory_outcome_written"))

            s = MemoryStore(td)
            idx = s._read_json(s.beads_dir / "index.json")
            beads = idx.get("beads") or {}
            hit = None
            for row in beads.values():
                src = [str(x) for x in (row.get("source_turn_ids") or [])]
                if "t-mem-outcome" in src:
                    hit = row
                    break
            self.assertIsNotNone(hit)
            self.assertEqual("memory_resolution", str((hit or {}).get("interaction_role") or ""))
            self.assertIsInstance((hit or {}).get("memory_outcome"), dict)


if __name__ == "__main__":
    unittest.main()
