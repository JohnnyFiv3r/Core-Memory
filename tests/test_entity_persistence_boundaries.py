import tempfile
import unittest
from unittest.mock import patch

import core_memory.entity as entity
import core_memory.entity.merge_flow as entity_merge_flow
import core_memory.entity.registry as entity_registry_exports
from core_memory.persistence import entity_merge_flow as persistence_merge_flow
from core_memory.persistence import entity_registry as persistence_entity_registry
from core_memory.persistence.store import MemoryStore


class TestEntityPersistenceBoundaries(unittest.TestCase):
    def test_entity_registry_exports_current_persistence_owner(self):
        self.assertIs(entity.normalize_entity_alias, persistence_entity_registry.normalize_entity_alias)
        self.assertIs(entity_registry_exports.load_entity_registry, persistence_entity_registry.load_entity_registry)
        self.assertIs(entity_registry_exports.sync_bead_entities_for_index, persistence_entity_registry.sync_bead_entities_for_index)

    def test_entity_merge_exports_current_persistence_owner(self):
        self.assertIs(entity.suggest_entity_merge_proposals, persistence_merge_flow.suggest_entity_merge_proposals)
        self.assertIs(entity_merge_flow.apply_entity_merge_direct, persistence_merge_flow.apply_entity_merge_direct)
        self.assertIs(entity_merge_flow.decide_entity_merge_proposal, persistence_merge_flow.decide_entity_merge_proposal)

    def test_memory_store_entity_merge_methods_delegate_to_persistence_owner(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)

            with patch("core_memory.persistence.entity_merge_flow.suggest_entity_merge_proposals", return_value={"ok": True}) as spy:
                out = store.suggest_entity_merge_proposals(min_score=0.5, max_pairs=2, source="unit")
            self.assertEqual({"ok": True}, out)
            spy.assert_called_once_with(store.root, min_score=0.5, max_pairs=2, source="unit")

            with patch("core_memory.persistence.entity_merge_flow.list_entity_merge_proposals", return_value=[{"id": "p1"}]) as spy:
                out = store.list_entity_merge_proposals(status="pending", limit=3)
            self.assertEqual([{"id": "p1"}], out)
            spy.assert_called_once_with(store.root, status="pending", limit=3)

            expected = {"ok": True, "status": "accepted"}
            with patch("core_memory.persistence.entity_merge_flow.decide_entity_merge_proposal", return_value=expected) as spy:
                out = store.decide_entity_merge_proposal(
                    "p1",
                    decision="accept",
                    reviewer="qa",
                    notes="same entity",
                    apply=True,
                    keep_entity_id="entity-a",
                )
            self.assertEqual(expected, out)
            spy.assert_called_once_with(
                store.root,
                proposal_id="p1",
                decision="accept",
                reviewer="qa",
                notes="same entity",
                apply=True,
                keep_entity_id="entity-a",
            )


if __name__ == "__main__":
    unittest.main()
