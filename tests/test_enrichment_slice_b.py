"""Tests for session enrichment delta Slice B (#9B).

Covers:
- enrichment_run_id parameter (UUID auto-generated when absent)
- Idempotency gate: second call with same run_id returns cached result, no duplicate stages
- Delta envelope written to .beads/events/enrichment-{bead_id}-{run_id[:8]}.jsonl
- All 9 stage_results keys present in the envelope
- _run_idempotency_token produces a stable sha256 hash
- Envelope path helper
"""
import json
import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.passes.enrichment import (
    _enrichment_envelope_path,
    _run_idempotency_token,
    _STAGE_RESULTS_DEFAULTS,
)


def _make_minimal_payload(**overrides) -> dict:
    base = {
        "session_id": "sess-test",
        "turn_id": "turn-001",
        "bead_id": "bead-001",
        "user_query": "hello",
        "assistant_final": "hi",
    }
    base.update(overrides)
    return base


def _call_enrichment(root: str, payload: dict, enrichment_run_id: str | None = None) -> dict:
    from core_memory.runtime.passes.enrichment import run_turn_enrichment
    return run_turn_enrichment(root=root, payload=payload, enrichment_run_id=enrichment_run_id)


class TestEnvelopePath(unittest.TestCase):
    def test_path_includes_bead_and_run_prefix(self):
        p = _enrichment_envelope_path("/some/root", "bead-abc", "run123456789")
        self.assertIn("enrichment-bead-abc-run12345", p.name)
        self.assertTrue(p.name.endswith(".jsonl"))

    def test_path_under_events_dir(self):
        p = _enrichment_envelope_path("/some/root", "bead-abc", "run12345678")
        self.assertEqual(p.parent.name, "events")

    def test_slash_in_bead_id_sanitized(self):
        p = _enrichment_envelope_path("/some/root", "bead/with/slash", "run12345678")
        self.assertNotIn("/", p.name)


class TestIdempotencyToken(unittest.TestCase):
    def test_stable_hash(self):
        t1 = _run_idempotency_token("bead-001", "run-abc")
        t2 = _run_idempotency_token("bead-001", "run-abc")
        self.assertEqual(t1, t2)

    def test_different_inputs_differ(self):
        t1 = _run_idempotency_token("bead-001", "run-abc")
        t2 = _run_idempotency_token("bead-002", "run-abc")
        self.assertNotEqual(t1, t2)

    def test_token_starts_with_sha256(self):
        t = _run_idempotency_token("bead-001", "run-abc")
        self.assertTrue(t.startswith("sha256:"))


class TestStageResultsDefaults(unittest.TestCase):
    def test_all_nine_keys_present(self):
        expected = {
            "association_pass", "claim_extraction", "preview_associations",
            "crawler_merge", "decision_pass", "claim_updates",
            "memory_outcome", "goal_lifecycle", "quality_metric",
        }
        self.assertEqual(set(_STAGE_RESULTS_DEFAULTS.keys()), expected)


class TestEnrichmentRunId(unittest.TestCase):
    def test_run_id_auto_generated_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".beads" / "events").mkdir(parents=True)
            result = _call_enrichment(tmp, _make_minimal_payload())
        self.assertIn("enrichment_run_id", result)
        self.assertTrue(len(result["enrichment_run_id"]) > 0)

    def test_supplied_run_id_preserved(self):
        run_id = "abc123deadbeef00abc123deadbeef00"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".beads" / "events").mkdir(parents=True)
            result = _call_enrichment(tmp, _make_minimal_payload(), enrichment_run_id=run_id)
        self.assertEqual(result["enrichment_run_id"], run_id)


class TestEnvelopePersistence(unittest.TestCase):
    def test_envelope_written_after_run(self):
        run_id = "testrun0"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".beads" / "events").mkdir(parents=True)
            result = _call_enrichment(tmp, _make_minimal_payload(bead_id="bead-x"), enrichment_run_id=run_id)
            env_path = _enrichment_envelope_path(tmp, "bead-x", run_id)
            self.assertTrue(env_path.exists(), f"Envelope should exist at {env_path}")
            envelope = json.loads(env_path.read_text(encoding="utf-8").strip())
            self.assertEqual(envelope["schema"], "session_enrichment_delta.v1")
            self.assertEqual(envelope["enrichment_run_id"], run_id)
            self.assertEqual(envelope["bead_id"], "bead-x")

    def test_envelope_has_all_nine_stage_results(self):
        run_id = "testrun1"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".beads" / "events").mkdir(parents=True)
            _call_enrichment(tmp, _make_minimal_payload(bead_id="bead-y"), enrichment_run_id=run_id)
            env_path = _enrichment_envelope_path(tmp, "bead-y", run_id)
            envelope = json.loads(env_path.read_text(encoding="utf-8").strip())
            stage_results = envelope.get("stage_results", {})
        expected_keys = set(_STAGE_RESULTS_DEFAULTS.keys())
        self.assertEqual(set(stage_results.keys()), expected_keys)

    def test_envelope_has_idempotency_token(self):
        run_id = "testrun2"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".beads" / "events").mkdir(parents=True)
            _call_enrichment(tmp, _make_minimal_payload(bead_id="bead-z"), enrichment_run_id=run_id)
            env_path = _enrichment_envelope_path(tmp, "bead-z", run_id)
            envelope = json.loads(env_path.read_text(encoding="utf-8").strip())
        self.assertTrue(envelope.get("idempotency_token", "").startswith("sha256:"))


class TestIdempotencyGate(unittest.TestCase):
    def test_second_call_returns_cached_result(self):
        run_id = "idemrun0"
        payload = _make_minimal_payload(bead_id="bead-idem")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".beads" / "events").mkdir(parents=True)
            first = _call_enrichment(tmp, payload, enrichment_run_id=run_id)
            second = _call_enrichment(tmp, payload, enrichment_run_id=run_id)
            self.assertTrue(second.get("idempotent"), "Second call should be marked idempotent")
            self.assertIn("stage_results", second)

    def test_different_run_id_does_not_gate(self):
        payload = _make_minimal_payload(bead_id="bead-idem2")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".beads" / "events").mkdir(parents=True)
            first = _call_enrichment(tmp, payload, enrichment_run_id="run-first")
            second = _call_enrichment(tmp, payload, enrichment_run_id="run-second")
            self.assertFalse(second.get("idempotent"), "Different run_id should not be gated")

    def test_stage_results_keys_preserved_through_idempotency_gate(self):
        run_id = "idemrun1"
        payload = _make_minimal_payload(bead_id="bead-idem3")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".beads" / "events").mkdir(parents=True)
            _call_enrichment(tmp, payload, enrichment_run_id=run_id)
            cached = _call_enrichment(tmp, payload, enrichment_run_id=run_id)
            stage_results = cached.get("stage_results", {})
        expected_keys = set(_STAGE_RESULTS_DEFAULTS.keys())
        self.assertEqual(set(stage_results.keys()), expected_keys)


if __name__ == "__main__":
    unittest.main()
