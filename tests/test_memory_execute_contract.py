import tempfile
import unittest

from core_memory.store import MemoryStore
from core_memory.tools.memory import execute


class TestMemoryExecuteContract(unittest.TestCase):
    def test_execute_response_shape(self):
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
            for key in ['request', 'snapped', 'results', 'chains', 'grounding', 'confidence', 'next_action']:
                self.assertIn(key, out)
            self.assertTrue((out.get('results') or []))


if __name__ == '__main__':
    unittest.main()
