import tempfile
import unittest

from core_memory.entity.registry import (
    ensure_entity_registry_for_index,
    normalize_entity_alias,
    resolve_entity_id,
    sync_bead_entities_for_index,
    upsert_canonical_entity,
)
from core_memory.persistence.store import MemoryStore


class TestEntityRegistry(unittest.TestCase):
    def test_normalize_entity_alias(self):
        self.assertEqual("openai", normalize_entity_alias("OpenAI, Inc."))
        self.assertEqual("acmelabs", normalize_entity_alias("Acme-Labs LLC"))

    def test_upsert_and_alias_resolution(self):
        idx = {"beads": {}, "associations": []}
        ensure_entity_registry_for_index(idx)

        first = upsert_canonical_entity(
            idx,
            label="OpenAI Inc.",
            aliases=["Open AI", "OpenAI"],
            confidence=0.8,
            provenance={"kind": "bead", "bead_id": "b1", "source": "test"},
        )
        self.assertTrue(first.get("ok"))
        eid = str(first.get("entity_id") or "")
        self.assertTrue(eid.startswith("entity-"))

        second = upsert_canonical_entity(
            idx,
            label="Open AI",
            aliases=["OpenAI"],
            confidence=0.6,
            provenance={"kind": "bead", "bead_id": "b2", "source": "test"},
        )
        self.assertTrue(second.get("ok"))
        self.assertEqual(eid, second.get("entity_id"))

        self.assertEqual(eid, resolve_entity_id(idx, "openai"))
        self.assertEqual(eid, resolve_entity_id(idx, "Open AI"))

    def test_sync_bead_entities_populates_entity_ids(self):
        idx = {"beads": {}, "associations": []}
        bead = {
            "id": "bead-1",
            "entities": ["OpenAI", "Open AI", "Acme Corp"],
        }
        out = sync_bead_entities_for_index(idx, bead, source="unit_test")
        self.assertTrue(out.get("ok"))
        self.assertEqual(2, int(out.get("linked") or 0))
        self.assertEqual(2, len(bead.get("entity_ids") or []))

    def test_memory_store_add_bead_writes_entity_registry(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bid = s.add_bead(
                type="context",
                title="Company note",
                summary=["Entity references"],
                entities=["OpenAI", "Open AI", "Acme"],
                session_id="main",
                source_turn_ids=["t1"],
            )
            idx = s._read_json(s.beads_dir / "index.json")
            bead = (idx.get("beads") or {}).get(bid) or {}
            registry = idx.get("entities") or {}
            alias_map = idx.get("entity_aliases") or {}

            self.assertGreaterEqual(len(registry), 2)
            self.assertGreaterEqual(len(alias_map), 2)
            self.assertEqual(2, len(bead.get("entity_ids") or []))


if __name__ == "__main__":
    unittest.main()
