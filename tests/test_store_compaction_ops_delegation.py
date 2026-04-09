from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreCompactionOpsDelegationSlice75A(unittest.TestCase):
    def test_compact_uncompact_myelinate_delegate(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-compaction-deleg-") as td:
            store = MemoryStore(td)

            expected_compact = {"ok": True, "compacted": 1}
            with patch("core_memory.persistence.store_compaction_ops.compact_for_store", return_value=expected_compact) as stub_compact:
                out_compact = store.compact(session_id="s1", promote=True, only_bead_ids=["b1"], skip_bead_ids=["b2"], force_archive_all=True)
            self.assertEqual(expected_compact, out_compact)
            self.assertEqual(1, stub_compact.call_count)

            expected_uncompact = {"ok": True, "id": "b1"}
            with patch("core_memory.persistence.store_compaction_ops.uncompact_for_store", return_value=expected_uncompact) as stub_uncompact:
                out_uncompact = store.uncompact("b1")
            self.assertEqual(expected_uncompact, out_uncompact)
            self.assertEqual(1, stub_uncompact.call_count)

            expected_myelinate = {"dry_run": True, "actions": []}
            with patch("core_memory.persistence.store_compaction_ops.myelinate_for_store", return_value=expected_myelinate) as stub_myelinate:
                out_myelinate = store.myelinate(apply=False)
            self.assertEqual(expected_myelinate, out_myelinate)
            self.assertEqual(1, stub_myelinate.call_count)


if __name__ == "__main__":
    unittest.main()
