import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.runtime.observability.myelination import (
    MYELINATION_MANIFEST_SCHEMA,
    read_myelination_manifest,
)


def _write_manifest(root, payload):
    p = Path(root) / ".beads" / "events" / "myelination-manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")


class TestReadMyelinationManifest(unittest.TestCase):
    def test_absent_reports_not_present(self):
        with tempfile.TemporaryDirectory() as td:
            out = read_myelination_manifest(td)
            self.assertTrue(out["ok"])
            self.assertFalse(out["present"])
            self.assertEqual(MYELINATION_MANIFEST_SCHEMA, out["schema"])

    def test_present_served_from_disk(self):
        with tempfile.TemporaryDirectory() as td:
            _write_manifest(td, {
                "schema": MYELINATION_MANIFEST_SCHEMA,
                "enabled": True,
                "bonus_by_edge_key": {"a|supports|b": 0.12},
                "bonus_by_bead_id": {"a": 0.06},
                "stats": {"edges": 1},
            })
            out = read_myelination_manifest(td)
            self.assertTrue(out["ok"])
            self.assertTrue(out["present"])
            self.assertEqual(0.12, out["bonus_by_edge_key"]["a|supports|b"])
            self.assertEqual(1, out["stats"]["edges"])

    def test_corrupt_manifest_reports_error(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".beads" / "events" / "myelination-manifest.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{not json", encoding="utf-8")
            out = read_myelination_manifest(td)
            self.assertFalse(out["ok"])
            self.assertEqual("myelination_manifest_unreadable", out["error"])


if __name__ == "__main__":
    unittest.main()
