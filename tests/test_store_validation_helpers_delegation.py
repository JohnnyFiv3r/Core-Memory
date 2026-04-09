from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreValidationHelpersDelegationSlice71A(unittest.TestCase):
    def test_normalize_links_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-valid-deleg-") as td:
            store = MemoryStore(td)
            with patch("core_memory.persistence.store_validation_helpers.normalize_links", return_value=[{"type": "supports", "bead_id": "b1"}]) as stub:
                out = store._normalize_links({"supports": ["b1"]})
            self.assertEqual([{"type": "supports", "bead_id": "b1"}], out)
            self.assertEqual(1, stub.call_count)

    def test_validate_bead_fields_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-valid-deleg-") as td:
            store = MemoryStore(td)
            bead = {"type": "lesson"}
            with patch("core_memory.persistence.store_validation_helpers.validate_bead_fields_for_store", return_value=None) as stub:
                out = store._validate_bead_fields(bead)
            self.assertIsNone(out)
            self.assertEqual(1, stub.call_count)
            args, _kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertIs(args[1], bead)


if __name__ == "__main__":
    unittest.main()
