from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore, DiagnosticError
from core_memory.schema.models import BeadType


class TestStoreJsonOpsDelegationSlice89A(unittest.TestCase):
    def test_read_write_normalize_delegate(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-json-deleg-") as td:
            store = MemoryStore(td)
            p = Path(td) / ".beads" / "index.json"

            with patch("core_memory.persistence.store_json_ops.read_json_for_store", return_value={"ok": True}) as stub_read:
                out = store._read_json(p)
            self.assertEqual({"ok": True}, out)
            self.assertEqual(1, stub_read.call_count)
            kwargs = stub_read.call_args.kwargs
            self.assertEqual(p, kwargs.get("path"))
            self.assertEqual(store.root, kwargs.get("root"))
            self.assertIs(DiagnosticError, kwargs.get("diagnostic_error_cls"))

            with patch("core_memory.persistence.store_json_ops.write_json_for_store", return_value=None) as stub_write:
                store._write_json(p, {"a": 1})
            self.assertEqual(1, stub_write.call_count)
            self.assertEqual(p, stub_write.call_args.kwargs.get("path"))
            self.assertEqual({"a": 1}, stub_write.call_args.kwargs.get("data"))

            with patch("core_memory.persistence.store_json_ops.normalize_enum_for_store", return_value="decision") as stub_norm:
                out_norm = store._normalize_enum(BeadType.DECISION, BeadType)
            self.assertEqual("decision", out_norm)
            self.assertEqual(1, stub_norm.call_count)

    def test_read_json_corruption_still_raises_diagnostic_error(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-json-deleg-") as td:
            store = MemoryStore(td)
            bad = Path(td) / ".beads" / "bad.json"
            bad.write_text("{not valid json", encoding="utf-8")

            with self.assertRaises(DiagnosticError) as ctx:
                store._read_json(bad)
            msg = str(ctx.exception)
            self.assertIn("Corrupt JSON file", msg)
            self.assertIn("Recovery:", msg)


if __name__ == "__main__":
    unittest.main()
