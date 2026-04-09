from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.backend import JsonFileBackend
from core_memory.persistence.store import (
    DiagnosticError,
    BEADS_DIR,
    TURNS_DIR,
    EVENTS_DIR,
    SESSION_FILE,
    INDEX_FILE,
    HEADS_FILE,
)
from core_memory.persistence.store_contract import DiagnosticError as ContractDiagnosticError


class TestStoreContractSlice93A(unittest.TestCase):
    def test_store_reexports_contract_symbols(self):
        self.assertIs(DiagnosticError, ContractDiagnosticError)
        self.assertEqual(".beads", BEADS_DIR)
        self.assertEqual(".turns", TURNS_DIR)
        self.assertEqual(".beads/events", EVENTS_DIR)
        self.assertEqual("session-{id}.jsonl", SESSION_FILE)
        self.assertEqual("index.json", INDEX_FILE)
        self.assertEqual("heads.json", HEADS_FILE)

    def test_json_backend_corruption_raises_diagnostic_error(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-contract-") as td:
            beads = Path(td) / ".beads"
            beads.mkdir(parents=True, exist_ok=True)
            (beads / "index.json").write_text("{not valid", encoding="utf-8")
            b = JsonFileBackend(beads)
            with self.assertRaises(DiagnosticError) as ctx:
                _ = b.load_index()
            msg = str(ctx.exception)
            self.assertIn("Corrupt JSON file", msg)
            self.assertIn("Recovery:", msg)


if __name__ == "__main__":
    unittest.main()
