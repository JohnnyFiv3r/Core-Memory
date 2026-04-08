from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreInitOpsSlice88A(unittest.TestCase):
    def test_init_respects_env_and_tenant_paths(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-init-") as td:
            with patch.dict(
                os.environ,
                {
                    "CORE_MEMORY_ASSOCIATE_ON_ADD": "0",
                    "CORE_MEMORY_ASSOCIATE_LOOKBACK": "55",
                    "CORE_MEMORY_ASSOCIATE_TOP_K": "7",
                    "CORE_MEMORY_STRICT_REQUIRED_FIELDS": "1",
                    "CORE_MEMORY_BEAD_SESSION_ID_MODE": "strict",
                    "CORE_MEMORY_AUTO_PROMOTE_ON_COMPACT": "1",
                },
                clear=False,
            ):
                store = MemoryStore(td, tenant_id="tenant-a")

            self.assertFalse(store.associate_on_add)
            self.assertEqual(55, store.assoc_lookback)
            self.assertEqual(7, store.assoc_top_k)
            self.assertTrue(store.strict_required_fields)
            self.assertEqual("strict", store.bead_session_id_mode)
            self.assertTrue(store.auto_promote_on_compact)
            self.assertIn("tenants", str(store.beads_dir))
            self.assertIn("tenant-a", str(store.beads_dir))

    def test_invalid_int_env_falls_back_defaults(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-init-") as td:
            with patch.dict(
                os.environ,
                {
                    "CORE_MEMORY_ASSOCIATE_LOOKBACK": "not-an-int",
                    "CORE_MEMORY_ASSOCIATE_TOP_K": "also-bad",
                },
                clear=False,
            ):
                store = MemoryStore(td)

            self.assertEqual(40, store.assoc_lookback)
            self.assertEqual(3, store.assoc_top_k)


if __name__ == "__main__":
    unittest.main()
