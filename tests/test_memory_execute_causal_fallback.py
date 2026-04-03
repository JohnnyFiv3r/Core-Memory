import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools.memory import execute


class TestMemoryExecuteCausalFallback(unittest.TestCase):
    def test_causal_returns_context_even_if_ungrounded(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"}, clear=False):
            s = MemoryStore(td)
            s.add_bead(type='decision', title='Promotion policy change', summary=['candidate first'], tags=['promotion_workflow'], session_id='main', source_turn_ids=['t1'])
            out = execute(
                {
                    'raw_query': 'why promotion policy changed',
                    'intent': 'causal',
                    'constraints': {'require_structural': True},
                    'facets': {'topic_keys': ['promotion_workflow']},
                    'k': 5,
                },
                root=td,
                explain=True,
            )
            self.assertTrue(out.get('ok'))
            self.assertTrue((out.get('results') or []))
            g = out.get('grounding') or {}
            self.assertIn('required', g)
            self.assertIn('achieved', g)
            self.assertIn('reason', g)


if __name__ == '__main__':
    unittest.main()
