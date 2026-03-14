import tempfile
import unittest

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline import memory_search_typed


class TestSearchTypedTimeRange(unittest.TestCase):
    def test_time_range_filters_results(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            old_id = s.add_bead(type='decision', title='Old item', summary=['legacy'], session_id='main', source_turn_ids=['t1'])
            new_id = s.add_bead(type='decision', title='New item', summary=['current'], session_id='main', source_turn_ids=['t2'])

            idx = s._read_json(s.beads_dir / 'index.json')
            beads = idx.get('beads') or {}
            beads[old_id]['created_at'] = '2025-01-01T00:00:00+00:00'
            beads[new_id]['created_at'] = '2026-01-01T00:00:00+00:00'
            s._write_json(s.beads_dir / 'index.json', idx)

            out = memory_search_typed(td, {
                'intent': 'remember',
                'query_text': 'item',
                'time_range': {'from': '2025-06-01T00:00:00Z', 'to': '2026-12-31T00:00:00Z'},
                'k': 10,
            }, explain=True)
            ids = [r.get('bead_id') for r in (out.get('results') or [])]
            self.assertIn(new_id, ids)
            self.assertNotIn(old_id, ids)

    def test_invalid_time_range_warns(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type='decision', title='Item', summary=['x'], session_id='main', source_turn_ids=['t1'])
            out = memory_search_typed(td, {
                'intent': 'remember',
                'query_text': 'item',
                'time_range': {'from': 'not-a-time'},
                'k': 5,
            }, explain=True)
            self.assertIn('invalid_time_range_ignored', out.get('warnings') or [])


if __name__ == '__main__':
    unittest.main()
