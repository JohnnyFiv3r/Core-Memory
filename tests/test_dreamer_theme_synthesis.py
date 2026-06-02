"""Tests for #12: proposed_theme_candidate synthesis, enqueue, and apply."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.runtime.dreamer.candidates import (
    _candidate_key,
    _hypothesis_type,
    _proposal_family,
    _benchmark_tags_for_hypothesis,
    enqueue_synthesized_themes,
)


def _cand(bead_src, bead_tgt, rel="transferable_lesson", score=0.7, cid=None):
    import uuid
    return {
        "id": cid or f"dc-{uuid.uuid4().hex[:8]}",
        "status": "unreviewed",
        "hypothesis_type": "transferable_lesson_candidate",
        "source_bead_id": bead_src,
        "target_bead_id": bead_tgt,
        "relationship": rel,
        "confidence": score,
    }


def _write_candidates(td, rows):
    p = Path(td) / ".beads" / "events" / "dreamer-candidates.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows), encoding="utf-8")


class TestHypothesisTypeMappings(unittest.TestCase):
    def test_proposed_theme_maps_to_candidate(self):
        self.assertEqual("proposed_theme_candidate", _hypothesis_type("proposed_theme"))

    def test_proposal_family_theme(self):
        self.assertEqual("theme", _proposal_family("proposed_theme_candidate"))

    def test_benchmark_tags_theme(self):
        tags = _benchmark_tags_for_hypothesis("proposed_theme_candidate")
        self.assertIn("causal_mechanism", tags)


class TestCandidateKeyTheme(unittest.TestCase):
    def test_theme_key_uses_related_bead_ids(self):
        row = {
            "hypothesis_type": "proposed_theme_candidate",
            "relationship": "transferable_lesson",
            "related_bead_ids": ["b1", "b3", "b2"],
        }
        key = _candidate_key(row)
        self.assertIn("proposed_theme_candidate", key)
        self.assertIn("transferable_lesson", key)
        self.assertIn("b1", key)
        self.assertIn("b2", key)
        self.assertIn("b3", key)

    def test_theme_key_order_independent(self):
        row_a = {
            "hypothesis_type": "proposed_theme_candidate",
            "relationship": "transferable_lesson",
            "related_bead_ids": ["b1", "b2", "b3"],
        }
        row_b = {
            "hypothesis_type": "proposed_theme_candidate",
            "relationship": "transferable_lesson",
            "related_bead_ids": ["b3", "b1", "b2"],
        }
        self.assertEqual(_candidate_key(row_a), _candidate_key(row_b))

    def test_regular_key_unchanged(self):
        row = {
            "hypothesis_type": "association_candidate",
            "source_bead_id": "b1",
            "target_bead_id": "b2",
            "relationship": "supports",
            "source_entity_id": "",
            "target_entity_id": "",
        }
        key = _candidate_key(row)
        self.assertNotIn("proposed_theme_candidate", key)


class TestSynthesizeThemes(unittest.TestCase):
    def test_three_candidates_sharing_bead_and_signal_emit_theme(self):
        from core_memory.runtime.dreamer.analysis import synthesize_themes

        with tempfile.TemporaryDirectory() as td:
            rows = [
                _cand("bead-A", "bead-B", "transferable_lesson", 0.8),
                _cand("bead-A", "bead-C", "transferable_lesson", 0.7),
                _cand("bead-A", "bead-D", "transferable_lesson", 0.6),
            ]
            _write_candidates(td, rows)
            themes = synthesize_themes(td)

        self.assertGreaterEqual(len(themes), 1)
        theme = themes[0]
        self.assertEqual("proposed_theme_candidate", theme["hypothesis_type"])
        self.assertEqual("theme", theme["proposal_family"])
        self.assertGreaterEqual(len(theme["related_bead_ids"]), 3)
        self.assertIn("bead-A", theme["related_bead_ids"])

    def test_two_candidates_below_threshold_no_theme(self):
        from core_memory.runtime.dreamer.analysis import synthesize_themes

        with tempfile.TemporaryDirectory() as td:
            rows = [
                _cand("bead-A", "bead-B", "transferable_lesson"),
                _cand("bead-A", "bead-C", "transferable_lesson"),
            ]
            _write_candidates(td, rows)
            themes = synthesize_themes(td)

        self.assertEqual([], themes)

    def test_low_confidence_candidates_excluded(self):
        from core_memory.runtime.dreamer.analysis import synthesize_themes

        with tempfile.TemporaryDirectory() as td:
            rows = [
                _cand("bead-A", "bead-B", "transferable_lesson", score=0.3),
                _cand("bead-A", "bead-C", "transferable_lesson", score=0.2),
                _cand("bead-A", "bead-D", "transferable_lesson", score=0.1),
            ]
            _write_candidates(td, rows)
            themes = synthesize_themes(td)

        self.assertEqual([], themes)

    def test_theme_candidates_excluded_from_synthesis_input(self):
        from core_memory.runtime.dreamer.analysis import synthesize_themes

        with tempfile.TemporaryDirectory() as td:
            # Three existing theme candidates should not generate a meta-theme
            rows = [
                {
                    "id": "dc-t1", "status": "unreviewed",
                    "hypothesis_type": "proposed_theme_candidate",
                    "source_bead_id": "bead-A", "target_bead_id": "bead-B",
                    "relationship": "transferable_lesson", "confidence": 0.8,
                    "related_bead_ids": ["bead-A", "bead-B", "bead-C"],
                },
                {
                    "id": "dc-t2", "status": "unreviewed",
                    "hypothesis_type": "proposed_theme_candidate",
                    "source_bead_id": "bead-A", "target_bead_id": "bead-D",
                    "relationship": "transferable_lesson", "confidence": 0.7,
                    "related_bead_ids": ["bead-A", "bead-D", "bead-E"],
                },
                {
                    "id": "dc-t3", "status": "unreviewed",
                    "hypothesis_type": "proposed_theme_candidate",
                    "source_bead_id": "bead-A", "target_bead_id": "bead-F",
                    "relationship": "transferable_lesson", "confidence": 0.6,
                    "related_bead_ids": ["bead-A", "bead-F", "bead-G"],
                },
            ]
            _write_candidates(td, rows)
            themes = synthesize_themes(td)

        self.assertEqual([], themes)

    def test_non_unreviewed_candidates_excluded(self):
        from core_memory.runtime.dreamer.analysis import synthesize_themes

        with tempfile.TemporaryDirectory() as td:
            rows = [
                {**_cand("bead-A", "bead-B"), "status": "accepted"},
                {**_cand("bead-A", "bead-C"), "status": "rejected"},
                {**_cand("bead-A", "bead-D"), "status": "deferred"},
            ]
            _write_candidates(td, rows)
            themes = synthesize_themes(td)

        self.assertEqual([], themes)

    def test_theme_confidence_is_mean_of_cluster(self):
        from core_memory.runtime.dreamer.analysis import synthesize_themes

        with tempfile.TemporaryDirectory() as td:
            rows = [
                _cand("hub", "bead-B", "transferable_lesson", 0.6),
                _cand("hub", "bead-C", "transferable_lesson", 0.8),
                _cand("hub", "bead-D", "transferable_lesson", 1.0),
            ]
            _write_candidates(td, rows)
            themes = synthesize_themes(td)

        self.assertEqual(1, len(themes))
        self.assertAlmostEqual(themes[0]["confidence"], round((0.6 + 0.8 + 1.0) / 3, 4), places=3)

    def test_myelination_boost_orders_clusters(self):
        from core_memory.runtime.dreamer.analysis import synthesize_themes

        with tempfile.TemporaryDirectory() as td:
            manifest_path = Path(td) / ".beads" / "events" / "myelination-manifest.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps({"bonus_by_bead_id": {"hot-hub": 0.10}, "enabled": True}),
                encoding="utf-8",
            )
            rows = [
                # cluster 1: hub = "hot-hub" (high bonus)
                _cand("hot-hub", "bead-B", "transferable_lesson", 0.6),
                _cand("hot-hub", "bead-C", "transferable_lesson", 0.6),
                _cand("hot-hub", "bead-D", "transferable_lesson", 0.6),
                # cluster 2: hub = "cold-hub" (no bonus)
                _cand("cold-hub", "bead-E", "structural_symmetry", 0.9),
                _cand("cold-hub", "bead-F", "structural_symmetry", 0.9),
                _cand("cold-hub", "bead-G", "structural_symmetry", 0.9),
            ]
            _write_candidates(td, rows)
            themes = synthesize_themes(td)

        self.assertEqual(2, len(themes))
        self.assertGreater(
            float(themes[0].get("myelination_boost") or 0.0),
            float(themes[1].get("myelination_boost") or 0.0),
        )

    def test_empty_candidates_file_returns_empty(self):
        from core_memory.runtime.dreamer.analysis import synthesize_themes

        with tempfile.TemporaryDirectory() as td:
            themes = synthesize_themes(td)

        self.assertEqual([], themes)


class TestEnqueueSynthesizedThemes(unittest.TestCase):
    def _theme(self, related_bead_ids, rel="transferable_lesson"):
        import uuid
        return {
            "id": f"dc-{uuid.uuid4().hex[:8]}",
            "status": "unreviewed",
            "hypothesis_type": "proposed_theme_candidate",
            "proposal_family": "theme",
            "related_bead_ids": related_bead_ids,
            "relationship": rel,
            "confidence": 0.7,
            "source_bead_id": "",
            "target_bead_id": "",
        }

    def test_valid_theme_enqueued(self):
        with tempfile.TemporaryDirectory() as td:
            theme = self._theme(["b1", "b2", "b3"])
            result = enqueue_synthesized_themes(td, [theme])

        self.assertTrue(result.get("ok"))
        self.assertEqual(1, result.get("added"))
        self.assertEqual(0, result.get("quarantined"))

    def test_theme_with_fewer_than_3_beads_quarantined(self):
        with tempfile.TemporaryDirectory() as td:
            theme = self._theme(["b1", "b2"])
            result = enqueue_synthesized_themes(td, [theme])

        self.assertTrue(result.get("ok"))
        self.assertEqual(0, result.get("added"))
        self.assertEqual(1, result.get("quarantined"))

    def test_duplicate_theme_not_added_twice(self):
        with tempfile.TemporaryDirectory() as td:
            theme = self._theme(["b1", "b2", "b3"])
            enqueue_synthesized_themes(td, [theme])
            result = enqueue_synthesized_themes(td, [theme])

        self.assertEqual(0, result.get("added"))

    def test_empty_list_is_no_op(self):
        with tempfile.TemporaryDirectory() as td:
            result = enqueue_synthesized_themes(td, [])

        self.assertTrue(result.get("ok"))
        self.assertEqual(0, result.get("added"))


class TestDecideProposedThemeCandidate(unittest.TestCase):
    def _enqueue_theme(self, td, related_bead_ids=None):
        import uuid
        from core_memory.runtime.dreamer.candidates import enqueue_synthesized_themes
        theme = {
            "id": f"dc-{uuid.uuid4().hex[:8]}",
            "status": "unreviewed",
            "hypothesis_type": "proposed_theme_candidate",
            "proposal_family": "theme",
            "related_bead_ids": related_bead_ids or ["b1", "b2", "b3"],
            "relationship": "transferable_lesson",
            "confidence": 0.75,
            "rationale": "3 candidates share transferable_lesson",
            "source_bead_id": "",
            "target_bead_id": "",
            "run_metadata": {"session_id": "test-session"},
        }
        enqueue_synthesized_themes(td, [theme])
        return theme["id"]

    def test_accept_apply_theme_calls_process_turn_finalized(self):
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate

        with tempfile.TemporaryDirectory() as td:
            cid = self._enqueue_theme(td)
            with patch("core_memory.runtime.engine.process_turn_finalized", return_value={"ok": True}) as mock_ptf:
                result = decide_dreamer_candidate(
                    root=td, candidate_id=cid, decision="accept", apply=True
                )

        self.assertTrue(result.get("ok"))
        applied = result.get("applied") or {}
        self.assertEqual("proposed_theme_bead_written", applied.get("application_mode"))
        self.assertTrue(mock_ptf.called)
        call_kwargs = mock_ptf.call_args[1]
        meta = call_kwargs.get("metadata") or {}
        theme_meta = meta.get("proposed_theme") or {}
        self.assertEqual("proposed_theme", theme_meta.get("type"))
        self.assertEqual("dreamer", theme_meta.get("generated_by"))

    def test_accept_apply_theme_quarantines_under_3_beads(self):
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate

        with tempfile.TemporaryDirectory() as td:
            # Force a theme with <3 beads through (bypassing enqueue quarantine)
            import uuid
            from core_memory.runtime.dreamer.candidates import _write_candidates
            bad_theme = {
                "id": "dc-bad",
                "status": "unreviewed",
                "hypothesis_type": "proposed_theme_candidate",
                "related_bead_ids": ["b1", "b2"],  # only 2
                "relationship": "transferable_lesson",
                "confidence": 0.7,
                "source_bead_id": "",
                "target_bead_id": "",
            }
            (Path(td) / ".beads" / "events").mkdir(parents=True)
            _write_candidates(td, [bad_theme])
            result = decide_dreamer_candidate(
                root=td, candidate_id="dc-bad", decision="accept", apply=True
            )

        applied = result.get("applied") or {}
        self.assertEqual("proposed_theme_quarantined", applied.get("application_mode"))
        self.assertFalse(applied.get("ok"))

    def test_reject_theme_no_apply(self):
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate

        with tempfile.TemporaryDirectory() as td:
            cid = self._enqueue_theme(td)
            result = decide_dreamer_candidate(
                root=td, candidate_id=cid, decision="reject",
                notes="not useful"
            )

        self.assertTrue(result.get("ok"))
        self.assertEqual("rejected", result.get("status"))
        self.assertIsNone(result.get("applied"))


class TestDreamerEvalThemeMetrics(unittest.TestCase):
    def test_theme_acceptance_rate_in_report(self):
        from core_memory.runtime.dreamer.eval import dreamer_eval_report

        with tempfile.TemporaryDirectory() as td:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            rows = [
                {
                    "id": "dc-t1", "created_at": now, "status": "accepted",
                    "hypothesis_type": "proposed_theme_candidate",
                    "decision": {"decision": "accept", "decided_at": now},
                    "source_bead_id": "", "target_bead_id": "",
                    "related_bead_ids": ["b1", "b2", "b3"],
                },
                {
                    "id": "dc-t2", "created_at": now, "status": "rejected",
                    "hypothesis_type": "proposed_theme_candidate",
                    "decision": {"decision": "reject", "decided_at": now},
                    "source_bead_id": "", "target_bead_id": "",
                    "related_bead_ids": ["b1", "b4", "b5"],
                },
            ]
            p = Path(td) / ".beads" / "events" / "dreamer-candidates.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(rows), encoding="utf-8")
            report = dreamer_eval_report(td)

        self.assertIn("theme_candidates", report["counts"])
        self.assertEqual(2, report["counts"]["theme_candidates"])
        self.assertEqual(1, report["counts"]["theme_accepted"])
        self.assertIn("theme_acceptance_rate", report["metrics"])
        self.assertAlmostEqual(0.5, report["metrics"]["theme_acceptance_rate"], places=4)


if __name__ == "__main__":
    unittest.main()
