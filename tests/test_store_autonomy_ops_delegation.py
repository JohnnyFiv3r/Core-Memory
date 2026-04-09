from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreAutonomyOpsDelegationSlice79A(unittest.TestCase):
    def test_append_autonomy_kpi_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-autonomy-deleg-") as td:
            store = MemoryStore(td)
            expected = {"ok": True, "run_id": "r1"}
            with patch(
                "core_memory.persistence.store_autonomy_ops.append_autonomy_kpi_for_store",
                return_value=expected,
            ) as stub:
                out = store.append_autonomy_kpi(run_id="r1", contradiction_resolved=True, contradiction_latency_turns=3)

            self.assertEqual(expected, out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertEqual("r1", kwargs.get("run_id"))
            self.assertTrue(kwargs.get("contradiction_resolved"))
            self.assertEqual(3, kwargs.get("contradiction_latency_turns"))

    def test_reinforcement_signals_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-autonomy-deleg-") as td:
            store = MemoryStore(td)
            expected = {"count": 2}
            index = {"beads": {}, "associations": []}
            bead = {"id": "bead-1"}
            with patch(
                "core_memory.persistence.store_autonomy_ops.reinforcement_signals_for_store",
                return_value=expected,
            ) as stub:
                out = store._reinforcement_signals(index, bead)

            self.assertEqual(expected, out)
            self.assertEqual(1, stub.call_count)
            args, _kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertIs(args[1], index)
            self.assertIs(args[2], bead)


if __name__ == "__main__":
    unittest.main()
