"""Tests for myelination wiring (#11): feedback recording and bonus application."""
import json
import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.agent import _apply_myelination_bonuses, _read_myelination_manifest
from core_memory.retrieval.contracts import EvidenceItem, RecallResult


def _result(*scores: float | None) -> RecallResult:
    evidence = [EvidenceItem(bead_id=f"bead-{i}", score=s) for i, s in enumerate(scores)]
    return RecallResult(evidence=evidence, status="answered")


class TestApplyMyelinationBonuses(unittest.TestCase):
    def test_bonus_increases_score(self):
        result = _result(0.5, 0.3)
        _apply_myelination_bonuses(result, {"bead-1": 0.2})
        scores = {e.bead_id: e.score for e in result.evidence}
        self.assertAlmostEqual(scores["bead-1"], 0.5, places=5)
        self.assertAlmostEqual(scores["bead-0"], 0.5, places=5)

    def test_score_clamped_to_one(self):
        result = _result(0.95)
        _apply_myelination_bonuses(result, {"bead-0": 0.12})
        self.assertLessEqual(result.evidence[0].score, 1.0)

    def test_negative_bonus_clamps_to_zero(self):
        result = _result(0.05)
        _apply_myelination_bonuses(result, {"bead-0": -0.08})
        self.assertGreaterEqual(result.evidence[0].score, 0.0)

    def test_evidence_re_sorted_after_bonus(self):
        result = _result(0.3, 0.5)
        _apply_myelination_bonuses(result, {"bead-0": 0.3})
        self.assertEqual(result.evidence[0].bead_id, "bead-0")

    def test_no_op_when_bonus_map_empty(self):
        result = _result(0.7, 0.3)
        _apply_myelination_bonuses(result, {})
        self.assertEqual(result.evidence[0].bead_id, "bead-0")

    def test_none_score_treated_as_zero(self):
        result = _result(None)
        _apply_myelination_bonuses(result, {"bead-0": 0.05})
        self.assertAlmostEqual(result.evidence[0].score, 0.05, places=5)

    def test_unknown_bead_id_not_affected(self):
        result = _result(0.4)
        _apply_myelination_bonuses(result, {"other-bead": 0.1})
        self.assertAlmostEqual(result.evidence[0].score, 0.4, places=5)


class TestReadMyelinationManifest(unittest.TestCase):
    def test_reads_bonus_by_bead_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".beads" / "events" / "myelination-manifest.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps({"bonus_by_bead_id": {"bead-abc": 0.08, "bead-xyz": -0.04}, "enabled": True}),
                encoding="utf-8",
            )
            result = _read_myelination_manifest(tmp)
        self.assertAlmostEqual(result["bead-abc"], 0.08, places=6)
        self.assertAlmostEqual(result["bead-xyz"], -0.04, places=6)

    def test_missing_manifest_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _read_myelination_manifest(tmp)
        self.assertEqual(result, {})

    def test_corrupt_manifest_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".beads" / "events" / "myelination-manifest.json"
            p.parent.mkdir(parents=True)
            p.write_text("not json", encoding="utf-8")
            result = _read_myelination_manifest(tmp)
        self.assertEqual(result, {})

    def test_near_zero_bonuses_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".beads" / "events" / "myelination-manifest.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps({"bonus_by_bead_id": {"bead-tiny": 1e-10}, "enabled": True}),
                encoding="utf-8",
            )
            result = _read_myelination_manifest(tmp)
        self.assertNotIn("bead-tiny", result)


class TestMyelinationJobKind(unittest.TestCase):
    def test_enqueue_myelination_update_accepted(self):
        from core_memory.runtime.queue.side_effect_queue import enqueue_side_effect_event
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".beads" / "events").mkdir(parents=True)
            result = enqueue_side_effect_event(root=tmp, kind="myelination-update")
        self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result}")
        self.assertFalse(result.get("duplicate"))

    def test_process_myelination_update_writes_manifest(self):
        from core_memory.runtime.queue.side_effect_queue import process_side_effect_event
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".beads" / "events").mkdir(parents=True)
            result = process_side_effect_event(root=tmp, kind="myelination-update", payload={})
            self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result}")
            manifest_path = Path(tmp) / ".beads" / "events" / "myelination-manifest.json"
            self.assertTrue(manifest_path.exists(), "Manifest should be written after processing")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertIn("bonus_by_bead_id", manifest)

    def test_enqueue_async_job_myelination_alias(self):
        from core_memory.runtime.queue.jobs import enqueue_async_job
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".beads" / "events").mkdir(parents=True)
            result = enqueue_async_job(tmp, kind="myelination")
        self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result}")
        self.assertEqual(result.get("kind"), "myelination-update")


if __name__ == "__main__":
    unittest.main()
