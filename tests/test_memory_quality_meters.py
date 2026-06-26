from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.memory import confirm_bead
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.candidates import _write_candidates
from core_memory.runtime.observability.calibration import compute_calibration_curve
from core_memory.runtime.observability.self_model_drift import compute_self_model_drift
from core_memory.runtime.observability.tension_meter import compute_tension_resolution_meter
from core_memory.runtime.observability.retrieval_feedback import record_retrieval_feedback
from core_memory.soul.store import propose_soul_update


def _write_manifest(root: str | Path, payload: dict) -> None:
    p = Path(root) / ".beads" / "events" / "myelination-manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _record_feedback(root: str | Path, edge: tuple[str, str, str], *, ok: bool) -> None:
    src, rel, dst = edge
    record_retrieval_feedback(
        root,
        request={"query": "q"},
        response={
            "ok": ok,
            "answer_outcome": "answer" if ok else "abstain",
            "results": [{"bead_id": dst, "score": 0.9}],
            "chains": [{"edges": [{"src": src, "rel": rel, "dst": dst}]}],
        },
    )


def _write_index_associations(root: str | Path, associations: list[dict]) -> None:
    p = Path(root) / ".beads" / "index.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    index: dict = {}
    if p.exists():
        try:
            index = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            index = {}
    index.setdefault("beads", {})
    index["associations"] = associations
    p.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class TestMemoryQualityMeters(unittest.TestCase):
    def test_calibration_curve_uses_effective_confidence_bands(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_CALIBRATION_MIN_EVENTS": "1"},
            clear=False,
        ):
            _write_manifest(
                td,
                {
                    "schema": "core_memory.myelination_manifest.v2",
                    "enabled": True,
                    "effective_confidence_by_edge_key": {
                        "a|supports|b": 0.55,
                        "c|supports|d": 0.95,
                    },
                    "bonus_by_edge_key": {},
                },
            )
            _record_feedback(td, ("a", "supports", "b"), ok=False)
            _record_feedback(td, ("c", "supports", "d"), ok=True)

            out = compute_calibration_curve(td)

            self.assertEqual("calibration_curve.v1", out["schema"])
            self.assertEqual("good", out["status"])
            self.assertEqual("open", out["auto_mode_gate"])
            self.assertEqual(2, out["event_count"])
            self.assertEqual(2, out["sample_count"])
            self.assertIsNotNone(out["expected_calibration_error"])
            self.assertIsNotNone(out["brier_score"])
            self.assertEqual(1.0, out["high_band_usefulness_rate"])
            self.assertGreaterEqual(float(out["spearman_rho"]), 0.7)

    def test_calibration_uses_index_judge_prior_with_real_manifest_shape(self):
        # The production manifest (PRD-A) only carries bonus_by_edge_key — it has
        # no effective_confidence_by_edge_key field. The X-axis must therefore use
        # each edge's stored judge_prior from index.json + the manifest bonus
        # (mirroring the BFS), not a flat default prior for every edge.
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_CALIBRATION_MIN_EVENTS": "1"},
            clear=False,
        ):
            _write_manifest(
                td,
                {
                    "schema": "core_memory.myelination_manifest.v2",
                    "enabled": True,
                    "bonus_by_edge_key": {"c|supports|d": 0.03},
                },
            )
            _write_index_associations(
                td,
                [
                    {"source_bead": "a", "target_bead": "b", "relationship": "supports", "confidence": 0.45},
                    {"source_bead": "c", "target_bead": "d", "relationship": "supports", "confidence": 0.92},
                ],
            )
            _record_feedback(td, ("a", "supports", "b"), ok=False)
            _record_feedback(td, ("c", "supports", "d"), ok=True)

            out = compute_calibration_curve(td)
            bands = {band["label"]: band for band in out["bands"]}

            # judge_prior came from index.json, not a flat default.
            self.assertNotIn("judge_prior_unavailable_for_some_edges", out["limitations"])
            # a|supports|b -> 0.45 (band <0.6); c|supports|d -> 0.92 + 0.03 = 0.95 (band >=0.9).
            self.assertEqual(1, bands["<0.6"]["recall_count"])
            self.assertEqual(1, bands[">=0.9"]["recall_count"])
            self.assertEqual(0.0, bands["<0.6"]["realized_usefulness_rate"])
            self.assertEqual(1.0, bands[">=0.9"]["realized_usefulness_rate"])
            self.assertLess(float(out["expected_calibration_error"]), 0.5)
            self.assertLess(float(out["brier_score"]), 0.5)
            self.assertEqual("good", out["status"])
            self.assertEqual("open", out["auto_mode_gate"])

    def test_calibration_flags_missing_judge_prior_for_unknown_edges(self):
        # Edges that appear in feedback but not in index.json fall back to the
        # default prior and the result advertises the limitation.
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_CALIBRATION_MIN_EVENTS": "1"},
            clear=False,
        ):
            _write_manifest(
                td,
                {"schema": "core_memory.myelination_manifest.v2", "enabled": True, "bonus_by_edge_key": {}},
            )
            _record_feedback(td, ("x", "supports", "y"), ok=True)

            out = compute_calibration_curve(td)

            self.assertIn("judge_prior_unavailable_for_some_edges", out["limitations"])

    def test_tension_meter_flags_pending_accumulation(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_TENSION_STALE_PENDING_THRESHOLD": "0"},
            clear=False,
        ):
            _write_candidates(
                td,
                [
                    {
                        "id": "cand-tension",
                        "hypothesis_type": "tension_candidate",
                        "status": "pending",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "tension_key": "speed-v-care",
                        "statement": "Speed is in tension with careful review.",
                    },
                    {
                        "id": "cand-tension-2",
                        "hypothesis_type": "tension_candidate",
                        "status": "pending",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "tension_key": "scope-v-speed",
                        "statement": "Scope is in tension with speed.",
                    },
                ],
            )

            out = compute_tension_resolution_meter(td)

            self.assertEqual("tension_resolution_meter.v1", out["schema"])
            self.assertEqual(2, out["pending_count"])
            self.assertIn(out["status"], {"accumulating", "stalled"})
            self.assertTrue(set(out["flags"]) & {"stale_accumulation", "zero_resolution", "high_accumulation"})
            self.assertIn("candidate", out["tensions_by_status"])
            self.assertIn("human review", out["human_review_reminder"].lower())

    def test_self_model_drift_flags_ungrounded_identity_revision(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(
                td,
                target_file="IDENTITY.md",
                entry_key="Craft",
                content="We value careful craft.",
                epistemic_status="endorsed",
                requires_approval=False,
            )

            out = compute_self_model_drift(td)

            self.assertEqual("self_model_drift_meter.v1", out["schema"])
            self.assertEqual("drifting", out["status"])
            self.assertEqual(1, out["ungrounded_update_count"])
            self.assertEqual("ungrounded_update", out["flagged_revisions"][0]["flag"])

    def test_self_model_drift_accepts_qualifying_behavior_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bead_id = store.add_bead(
                type="decision",
                title="Choose careful craft",
                summary=["summary"],
                session_id="s1",
            )
            confirm_bead(td, bead_id)
            propose_soul_update(
                td,
                target_file="IDENTITY.md",
                entry_key="Craft",
                content="We value careful craft.",
                epistemic_status="endorsed",
                evidence=[{"type": "bead", "id": bead_id}],
                requires_approval=False,
            )

            out = compute_self_model_drift(td)

            self.assertEqual("healthy", out["status"])
            self.assertEqual(0, out["drift_score"])

    def test_quality_meter_http_endpoints(self):
        try:
            from fastapi.testclient import TestClient
            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

        with tempfile.TemporaryDirectory() as td:
            c = TestClient(app)
            calibration = c.get("/v1/myelination/calibration", params={"root": td})
            tension = c.get("/v1/soul/tension-meter", params={"root": td})
            drift = c.get("/v1/soul/self-model-drift", params={"root": td})

            self.assertEqual(200, calibration.status_code)
            self.assertEqual("calibration_curve.v1", calibration.json()["schema"])
            self.assertEqual(200, tension.status_code)
            self.assertEqual("tension_resolution_meter.v1", tension.json()["schema"])
            self.assertEqual(200, drift.status_code)
            self.assertEqual("self_model_drift_meter.v1", drift.json()["schema"])


if __name__ == "__main__":
    unittest.main()
