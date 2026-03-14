import tempfile
import unittest

from core_memory.store import MemoryStore
from core_memory.retrieval.tools.memory import execute


class TestMemoryExecuteSurfaceMetadata(unittest.TestCase):
    def test_execute_includes_surface_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type='decision', title='Candidate-first promotion', summary=['promotion workflow'], tags=['promotion_workflow'], session_id='main', source_turn_ids=['t1'])
            out = execute(
                {
                    'raw_query': 'remember candidate-first promotion',
                    'intent': 'remember',
                    'constraints': {'require_structural': False},
                    'facets': {'topic_keys': ['promotion_workflow']},
                    'k': 5,
                },
                root=td,
                explain=True,
            )
            self.assertTrue(out.get('ok'))
            self.assertIn(out.get('source_surface'), {'archive_graph', 'session_bead', 'rolling_window', 'transcript', 'memory_md'})
            self.assertIn(out.get('source_scope'), {'immediate', 'durable', 'historical'})
            self.assertTrue(isinstance(out.get('source_priority_applied'), list))


if __name__ == '__main__':
    unittest.main()
