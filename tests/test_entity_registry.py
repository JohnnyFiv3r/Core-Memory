import tempfile
import unittest
from unittest.mock import patch

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

    def test_sync_bead_entities_uses_llm_judge_for_aliases(self):
        idx = {"beads": {}, "associations": []}
        bead = {
            "id": "bead-1",
            "title": "OpenAI Platform migration",
            "summary": ["Open AI Platform and GPT-4o were selected for extraction."],
            "entities": ["Open AI Platform", "GPT-4o"],
        }
        judged = {
            "entities": [
                {
                    "label": "OpenAI Platform",
                    "aliases": ["Open AI Platform", "OpenAI Platform"],
                    "kind": "product",
                    "evidence": "Open AI Platform",
                    "confidence": 0.9,
                },
                {
                    "label": "GPT-4o",
                    "aliases": ["GPT-4o"],
                    "kind": "system",
                    "evidence": "GPT-4o",
                    "confidence": 0.88,
                },
            ]
        }
        with patch("core_memory.entity.registry._llm_judge_entities_anthropic", return_value=judged), patch(
            "core_memory.entity.registry._llm_judge_entities_openai", return_value=None
        ), patch.dict("os.environ", {"CORE_MEMORY_ENTITY_EXTRACTOR_MODE": "auto"}, clear=False):
            out = sync_bead_entities_for_index(idx, bead, source="unit_test")

        self.assertEqual("llm", out.get("judge"))
        self.assertEqual(2, int(out.get("linked") or 0))
        self.assertEqual(["OpenAI Platform", "GPT-4o"], bead.get("entities"))
        self.assertEqual(resolve_entity_id(idx, "Open AI Platform"), resolve_entity_id(idx, "OpenAI Platform"))
        first = next(iter((idx.get("entities") or {}).values()))
        self.assertEqual("llm", (first.get("provenance") or [{}])[-1].get("judge"))

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
