import json
import tempfile
import unittest
from pathlib import Path

from core_memory.store import MemoryStore
from core_memory.tools.memory_search import search_typed


class TestMemorySearchSmokeEval(unittest.TestCase):
    def test_three_basic_intents(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type='decision', title='Candidate-first promotion', summary=['promotion workflow'], tags=['promotion_workflow'], session_id='main', source_turn_ids=['t1'])
            s.add_bead(type='evidence', title='Structural sync pipeline', summary=['associations links edges graph'], tags=['structural_sync'], session_id='main', source_turn_ids=['t2'])
            s.add_bead(type='outcome', title='Graph archive retrieval shipped', summary=['memory reason retrieval'], tags=['graph_archive_retrieval'], session_id='main', source_turn_ids=['t3'])

            cases = [
                {'intent': 'causal', 'query_text': 'why candidate-first promotion', 'topic_keys': ['promotion_workflow'], 'k': 5, 'require_structural': False},
                {'intent': 'what_changed', 'query_text': 'what changed structural sync', 'topic_keys': ['structural_sync'], 'k': 5},
                {'intent': 'remember', 'query_text': 'remember graph archive retrieval', 'topic_keys': ['graph_archive_retrieval'], 'k': 5},
            ]

            for sub in cases:
                out = search_typed(sub, root=td, explain=True)
                self.assertTrue(out.get('ok'))
                self.assertTrue((out.get('results') or []))
                self.assertIn(out.get('confidence'), {'high', 'medium', 'low'})
                self.assertIn(out.get('suggested_next'), {'answer', 'broaden', 'ask_clarifying'})


if __name__ == '__main__':
    unittest.main()
