from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreAddBeadDelegationSlice77A(unittest.TestCase):
    def test_add_bead_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-add-bead-deleg-") as td:
            store = MemoryStore(td)
            with patch("core_memory.persistence.store_add_bead_ops.add_bead_for_store", return_value="bead-abc") as stub:
                out = store.add_bead(
                    type="decision",
                    title="Use canary",
                    summary=["rollout safely"],
                    because=["reduce risk"],
                    source_turn_ids=["t1"],
                    session_id="s1",
                    tags=["release"],
                )

            self.assertEqual("bead-abc", out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertEqual("decision", kwargs.get("type"))
            self.assertEqual("Use canary", kwargs.get("title"))
            self.assertEqual(["t1"], kwargs.get("source_turn_ids"))
            self.assertEqual("s1", kwargs.get("session_id"))


if __name__ == "__main__":
    unittest.main()
