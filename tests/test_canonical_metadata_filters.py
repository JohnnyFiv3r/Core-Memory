from __future__ import annotations

import unittest

from core_memory.retrieval.pipeline.canonical import _apply_typed_filters, _metadata_constraints, _to_anchor


class TestCanonicalMetadataFilters(unittest.TestCase):
    def test_metadata_filters_and_terms_match_nested_trace_metadata(self):
        rows = [
            {
                "bead_id": "bead-1",
                "session_id": "locomo:conv-49",
                "semantic_text": "Evan mentioned the Prius",
                "lexical_text": "",
            },
            {
                "bead_id": "bead-2",
                "session_id": "locomo:conv-30",
                "semantic_text": "Evan mentioned the Prius",
                "lexical_text": "",
            },
        ]
        by_id = {
            "bead-1": {
                "bead": {
                    "id": "bead-1",
                    "session_id": "locomo:conv-49",
                    "metadata": {
                        "sample_id": "conv-49",
                        "session_index": 3,
                        "source": "locomo",
                    },
                }
            },
            "bead-2": {
                "bead": {
                    "id": "bead-2",
                    "session_id": "locomo:conv-30",
                    "metadata": {"sample_id": "conv-30", "session_index": 3},
                }
            },
        }

        out, warnings = _apply_typed_filters(
            rows,
            by_id,
            {},
            {
                "scope": "project",  # absent legacy scope should not drop rows
                "topic_keys": ["sample:conv-49"],  # absent legacy topics should not drop rows
                "metadata": {"sample_id": "conv-49", "session_id": "locomo:conv-49"},
                "must_terms": ["sample_id=conv-49", "session_index=3"],
            },
        )

        self.assertEqual([], warnings)
        self.assertEqual(["bead-1"], [r["bead_id"] for r in out])

    def test_benchmark_control_constraints_do_not_become_metadata_filters(self):
        out = _metadata_constraints(
            {
                "benchmark_name": "locomo",
                "conversation_id": "locomo:conv-26",
                "qa_id": "locomo:conv-26:q0001",
                "recall_scope": "full_bead_corpus",
                "require_structural": False,
            }
        )

        self.assertEqual({"conversation_id": "locomo:conv-26"}, out)

    def test_anchor_exposes_locomo_dia_ids_from_metadata_and_turn_ids(self):
        anchor = _to_anchor(
            {"bead_id": "bead-1", "score": 0.9},
            {
                "bead-1": {
                    "session_id": "locomo:conv-26",
                    "bead": {
                        "id": "bead-1",
                        "title": "Caroline support group",
                        "type": "context",
                        "status": "open",
                        "source_turn_ids": ["locomo:conv-26:D1:3"],
                        "metadata": {"locomo_dia_ids": ["D1:3"]},
                    },
                }
            },
        )

        self.assertEqual(["D1:3"], anchor["dia_ids"])
        self.assertEqual(["D1:3"], anchor["locomo_dia_ids"])
        self.assertEqual(["locomo:conv-26:D1:3"], anchor["source_turn_ids"])


if __name__ == "__main__":
    unittest.main()
