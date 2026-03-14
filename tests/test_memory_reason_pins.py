import tempfile
import unittest

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools.memory import reason


class TestMemoryReasonPins(unittest.TestCase):
    def test_reason_accepts_and_surfaces_pins(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bid = s.add_bead(type='decision', title='Candidate-first promotion', summary=['promotion workflow'], tags=['promotion_workflow'], session_id='main', source_turn_ids=['t1'])
            out = reason(
                query='why candidate-first promotion',
                root=td,
                k=6,
                pinned_topic_keys=['promotion_workflow'],
                pinned_bead_ids=[bid],
                pinned_incident_ids=['promotion_inflation_2026q1'],
            )
            self.assertTrue(out.get('ok'))
            intent = out.get('intent') or {}
            self.assertIn('pinned_topic_keys', intent)
            self.assertIn('pinned_bead_ids', intent)
            self.assertIn('pinned_incident_ids', intent)


if __name__ == '__main__':
    unittest.main()
