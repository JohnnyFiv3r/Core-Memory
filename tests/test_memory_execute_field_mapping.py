import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools.memory import execute


class TestMemoryExecuteFieldMapping(unittest.TestCase):
    def test_execute_maps_request_fields_to_snapped_form(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"}, clear=False):
            s = MemoryStore(td)
            s.add_bead(type='decision', title='Candidate-first promotion', summary=['promotion workflow'], tags=['promotion_workflow'], session_id='main', source_turn_ids=['t1'])
            req = {
                'raw_query': 'remember candidate-first promotion',
                'intent': 'remember',
                'constraints': {'require_structural': False},
                'facets': {
                    'topic_keys': ['promotion_workflow'],
                    'bead_types': ['decision'],
                    'relation_types': ['supports'],
                    'must_terms': ['candidate', 'promotion'],
                    'avoid_terms': ['deprecated'],
                    'time_range': {'from': '2026-01-01T00:00:00Z', 'to': '2026-12-31T00:00:00Z'},
                },
                'k': 7,
            }
            out = execute(req, root=td, explain=True)
            self.assertTrue(out.get('ok'))
            snapped = out.get('snapped') or {}
            self.assertEqual(7, snapped.get('k'))
            self.assertEqual(['candidate', 'promotion'], snapped.get('must_terms'))
            self.assertEqual(['deprecated'], snapped.get('avoid_terms'))
            self.assertIn('time_range', snapped)

    def test_execute_causal_applies_facet_narrowing_to_trace_anchors(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"}, clear=False):
            s = MemoryStore(td)
            decision_id = s.add_bead(
                type='decision',
                title='Candidate-first promotion',
                summary=['promotion workflow'],
                tags=['promotion_workflow'],
                session_id='main',
                source_turn_ids=['t1'],
            )
            evidence_id = s.add_bead(
                type='evidence',
                title='Other evidence',
                summary=['promotion workflow'],
                tags=['other_topic'],
                session_id='main',
                source_turn_ids=['t2'],
            )

            req = {
                'raw_query': 'promotion workflow',
                'intent': 'causal',
                'facets': {
                    'topic_keys': ['promotion_workflow'],
                    'bead_types': ['decision'],
                },
                'k': 10,
            }
            out = execute(req, root=td, explain=True)
            self.assertTrue(out.get('ok'))

            snapped = out.get('snapped') or {}
            self.assertEqual(['promotion_workflow'], snapped.get('topic_keys'))
            self.assertEqual(['decision'], snapped.get('bead_types'))

            ids = [r.get('bead_id') for r in (out.get('results') or [])]
            self.assertIn(decision_id, ids)
            self.assertNotIn(evidence_id, ids)


if __name__ == '__main__':
    unittest.main()
