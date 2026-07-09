from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.store import MemoryStore, DiagnosticError


class TestStoreJsonDiagnostics(unittest.TestCase):
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
