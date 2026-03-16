import tempfile
import unittest

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools.memory import execute


class TestMemoryExecuteNextActionPolicy(unittest.TestCase):
    def test_remember_prefers_answer_over_clarify_when_results_exist(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type='outcome', title='Retrieval hardening shipped', summary=['graph archive retrieval'], tags=['graph_archive_retrieval'], session_id='main', source_turn_ids=['t1'])
            s.add_bead(type='decision', title='Reranker tuning', summary=['quality gate'], tags=['graph_archive_retrieval'], session_id='main', source_turn_ids=['t2'])
            out = execute(
                {
                    'raw_query': 'remember retrieval hardening work',
                    'intent': 'remember',
                    'constraints': {'require_structural': False},
                    'facets': {'topic_keys': ['graph_archive_retrieval']},
                    'k': 5,
                },
                root=td,
                explain=True,
            )
            self.assertTrue(out.get('ok'))
            self.assertTrue((out.get('results') or []))
            self.assertIn(out.get('next_action'), {'answer', 'broaden'})


if __name__ == '__main__':
    unittest.main()
