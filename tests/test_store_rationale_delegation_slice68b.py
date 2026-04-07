from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreRationaleDelegationSlice68B(unittest.TestCase):
    def test_infer_target_bead_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-rationale-deleg-") as td:
            store = MemoryStore(td)
            expected = {"id": "bead-AAA", "type": "decision"}
            with patch("core_memory.reporting.store_rationale.infer_target_bead_for_question", return_value=expected) as stub:
                out = store._infer_target_bead_for_question("why did we choose this?")
            self.assertEqual(expected, out)
            self.assertEqual(1, stub.call_count)
            args, _kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertEqual("why did we choose this?", args[1])

    def test_evaluate_rationale_recall_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-rationale-deleg-") as td:
            store = MemoryStore(td)
            expected = {"score": 2, "target_bead_id": "bead-AAA"}
            with patch("core_memory.reporting.store_rationale.evaluate_rationale_recall_for_store", return_value=expected) as stub:
                out = store.evaluate_rationale_recall("q", "a", bead_id="bead-AAA")
            self.assertEqual(expected, out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertEqual("q", args[1])
            self.assertEqual("a", args[2])
            self.assertEqual("bead-AAA", kwargs.get("bead_id"))


if __name__ == "__main__":
    unittest.main()
