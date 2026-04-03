import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools.memory import execute


class TestMemoryExecuteContract(unittest.TestCase):
    def test_execute_response_shape(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"}, clear=False):
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
            self.assertEqual('memory_execute_result.v1', out.get('schema_version'))
            self.assertEqual('memory_execute', out.get('contract'))
            for key in ['request', 'snapped', 'results', 'chains', 'grounding', 'confidence', 'next_action']:
                self.assertIn(key, out)
            self.assertIsInstance(out.get('results') or [], list)
            self.assertIsInstance(out.get('chains') or [], list)

            # Contract truth: memory.execute may return 0 direct results while still
            # providing grounded causal reasoning/chains and a valid next action.
            if not (out.get('results') or []):
                self.assertIn(out.get('next_action'), {'ask_clarifying', 'ask_followup', 'answer'})
                self.assertTrue(isinstance(out.get('warnings') or [], list))


if __name__ == '__main__':
    unittest.main()
