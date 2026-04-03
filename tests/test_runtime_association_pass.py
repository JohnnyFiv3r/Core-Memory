import tempfile
import unittest

from core_memory.runtime.association_pass import run_association_pass
from core_memory.persistence.store import MemoryStore


class TestRuntimeAssociationPass(unittest.TestCase):
    def test_runtime_entrypoint_applies_association_update(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t0"])
            b2 = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t1"])

            out = run_association_pass(
                root=td,
                session_id="s1",
                updates={
                    "associations": [
                        {
                            "source_bead_id": b1,
                            "target_bead_id": b2,
                            "relationship": "supports",
                            "confidence": 0.9,
                            "reason_text": "A supports B",
                        }
                    ]
                },
                visible_bead_ids=[b1, b2],
            )

            self.assertTrue(out.get("ok"))
            self.assertGreaterEqual(int(out.get("associations_appended") or 0), 1)


if __name__ == "__main__":
    unittest.main()
