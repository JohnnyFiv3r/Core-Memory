import tempfile
import unittest

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline import memory_search_typed


class TestMemorySearchTypedConfidenceUnified(unittest.TestCase):
    def test_confidence_diagnostics_present(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type='outcome', title='Retrieval hardening', summary=['graph archive retrieval'], tags=['graph_archive_retrieval'], session_id='main', source_turn_ids=['t1'])
            out = memory_search_typed(td, {
                'intent': 'remember',
                'query_text': 'remember retrieval hardening',
                'topic_keys': ['graph_archive_retrieval'],
                'k': 5,
            }, explain=True)
            self.assertIn(out.get('confidence'), {'high', 'medium', 'low'})
            self.assertIn(out.get('suggested_next'), {'answer', 'broaden', 'ask_clarifying'})
            ex = out.get('explain') or {}
            self.assertIn('confidence_diagnostics', ex)


if __name__ == '__main__':
    unittest.main()
