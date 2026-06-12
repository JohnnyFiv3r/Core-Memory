"""Storylines: overlay schema gate, convergence detection, decide-flow
materialisation, the one-way invariant, and the projection.

The epistemic contract under test:
- backbone derivation is byte-identical with and without overlays present
  (interpretation never becomes input to history)
- overlays are untraceable ⇒ rejected at the schema gate
- accepting a narrative candidate writes exactly one overlay record and
  zero beads / associations / claims (observer contract)
- supersession keeps history (revisable ≠ mutable)
- overlays are earned: no convergence ⇒ no candidates
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from core_memory.graph.storylines import (
    derive_storylines,
    overlays_path,
    read_active_overlays,
)
from core_memory.graph.worldlines import derive_worldlines
from core_memory.runtime.dreamer.candidates import (
    decide_dreamer_candidate,
    list_dreamer_candidates,
)
from core_memory.runtime.dreamer.convergence import (
    detect_worldline_convergence,
    enqueue_narrative_candidates,
)
from core_memory.schema.storyline_overlay import (
    ERROR_OVERLAY_UNTRACEABLE,
    build_storyline_overlay,
    validate_storyline_overlay,
)


def _write_index(root: Path, beads: dict, associations: list) -> None:
    beads_dir = root / ".beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    (beads_dir / "index.json").write_text(
        json.dumps({"beads": beads, "associations": associations}), encoding="utf-8"
    )


def _bead(title: str, created_at: str, *, type_: str = "context", entities: list | None = None) -> dict:
    return {
        "type": type_,
        "title": title,
        "summary": [title],
        "session_id": "s1",
        "created_at": created_at,
        "retrieval_eligible": True,
        "status": "open",
        "entities": entities or [],
    }


def _convergent_fixture(root: Path) -> None:
    """Two entity threads + one goal arc crossing in two shared beads."""
    beads = {
        "bead-AAAAAAAAAAA1": _bead("kickoff", "2026-01-01T00:00:00+00:00", entities=["acme"]),
        "bead-AAAAAAAAAAA2": _bead("acme demo on pipeline", "2026-02-01T00:00:00+00:00", entities=["acme", "pipeline"]),
        "bead-AAAAAAAAAAA3": _bead("pipeline fix for acme", "2026-03-01T00:00:00+00:00", entities=["acme", "pipeline"]),
        "bead-GOAL00000001": _bead("win acme", "2026-01-05T00:00:00+00:00", type_="goal", entities=["acme"]),
        "bead-AAAAAAAAAAA4": _bead("pipeline docs", "2026-02-15T00:00:00+00:00", entities=["pipeline"]),
    }
    associations = [
        {"source_bead": "bead-AAAAAAAAAAA3", "target_bead": "bead-GOAL00000001", "relationship": "resolves", "status": "active"},
    ]
    _write_index(root, beads, associations)


def _flat_fixture(root: Path) -> None:
    """Disjoint threads — no shared beads, no convergence."""
    beads = {
        "bead-BBBBBBBBBBB1": _bead("a1", "2026-01-01T00:00:00+00:00", entities=["alpha"]),
        "bead-BBBBBBBBBBB2": _bead("a2", "2026-02-01T00:00:00+00:00", entities=["alpha"]),
        "bead-BBBBBBBBBBB3": _bead("b1", "2026-01-10T00:00:00+00:00", entities=["beta"]),
        "bead-BBBBBBBBBBB4": _bead("b2", "2026-02-10T00:00:00+00:00", entities=["beta"]),
    }
    _write_index(root, beads, [])


class TestOverlaySchemaGate(unittest.TestCase):
    def test_untraceable_overlay_rejected(self):
        overlay = build_storyline_overlay(
            kind="narrative", statement="meaning without history",
            supporting_worldline_ids=[], supporting_bead_ids=[], confidence=0.8,
        )
        ok, code, _ = validate_storyline_overlay(overlay)
        self.assertFalse(ok)
        self.assertEqual(ERROR_OVERLAY_UNTRACEABLE, code)

    def test_valid_overlay_accepted_with_falsifiers(self):
        overlay = build_storyline_overlay(
            kind="narrative", statement="threads converge",
            supporting_worldline_ids=["wl-1"], supporting_bead_ids=["bead-X"],
            confidence=0.7, expected_revision_triggers=["goal resolves against pattern"],
        )
        ok, code, _ = validate_storyline_overlay(overlay)
        self.assertTrue(ok, code)
        self.assertEqual(["goal resolves against pattern"], overlay["expected_revision_triggers"])


class TestConvergenceDetection(unittest.TestCase):
    def test_planted_convergence_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _convergent_fixture(root)
            detections = detect_worldline_convergence(root)
            self.assertEqual(1, len(detections))
            det = detections[0]
            self.assertGreaterEqual(len(det["worldline_ids"]), 2)
            self.assertGreaterEqual(len(det["shared_bead_ids"]), 2)
            self.assertGreaterEqual(len(det["kinds"]), 2, "kind diversity expected (entity+goal)")
            self.assertTrue(det["revision_triggers"])

    def test_no_convergence_emits_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _flat_fixture(root)
            self.assertEqual([], detect_worldline_convergence(root))
            out = enqueue_narrative_candidates(root)
            self.assertEqual(0, out["enqueued"])

    def test_enqueue_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _convergent_fixture(root)
            first = enqueue_narrative_candidates(root)
            self.assertEqual(1, first["enqueued"])
            second = enqueue_narrative_candidates(root)
            self.assertEqual(0, second["enqueued"], "pending candidate must block re-emission")


class TestDecideFlowMaterialisation(unittest.TestCase):
    def _accept_first_candidate(self, root: Path) -> dict:
        enqueue_narrative_candidates(root)
        pending = list_dreamer_candidates(root=root, status="pending")["results"]
        narrative = [r for r in pending if r["hypothesis_type"] == "narrative_candidate"]
        self.assertEqual(1, len(narrative))
        return decide_dreamer_candidate(
            root=root, candidate_id=narrative[0]["id"], decision="accept",
            reviewer="test", apply=True,
        )

    def test_retried_accept_is_idempotent(self):
        # A double-submit / timeout retry of the same decision must return the
        # original application — one decision, at most one overlay. Without
        # this, a retry appends a duplicate revision that supersedes its own
        # predecessor, inflating history without any new review.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _convergent_fixture(root)
            first = self._accept_first_candidate(root)
            cid = first["candidate_id"]
            overlay_id = first["applied"]["overlay_id"]

            retry = decide_dreamer_candidate(
                root=root, candidate_id=cid, decision="accept",
                reviewer="test", apply=True,
            )
            self.assertTrue(retry["ok"])
            self.assertEqual("already_applied", retry["applied"]["application_mode"])
            self.assertEqual(overlay_id, retry["applied"]["overlay_id"])

            overlays = read_active_overlays(root)
            self.assertEqual(1, len(overlays))
            self.assertEqual(overlay_id, overlays[0]["id"])
            # History not inflated: exactly one record total.
            storylines = derive_storylines(root)["storylines"]
            self.assertEqual(
                1, max(s["overlay_history_count"] for s in storylines if s["overlays"]),
            )

    def test_accept_writes_one_overlay_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _convergent_fixture(root)
            index_before = (root / ".beads" / "index.json").read_text(encoding="utf-8")

            out = self._accept_first_candidate(root)
            self.assertTrue(out["ok"], out)
            self.assertEqual("storyline_overlay_written", out["applied"]["application_mode"])

            overlays = read_active_overlays(root)
            self.assertEqual(1, len(overlays))
            ok, code, _ = validate_storyline_overlay(overlays[0])
            self.assertTrue(ok, code)

            # Observer contract: the grounded store is untouched.
            index_after = (root / ".beads" / "index.json").read_text(encoding="utf-8")
            self.assertEqual(index_before, index_after)

    def test_reject_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _convergent_fixture(root)
            enqueue_narrative_candidates(root)
            pending = list_dreamer_candidates(root=root, status="pending")["results"]
            cid = [r for r in pending if r["hypothesis_type"] == "narrative_candidate"][0]["id"]
            decide_dreamer_candidate(root=root, candidate_id=cid, decision="reject", apply=True)
            self.assertEqual([], read_active_overlays(root))
            self.assertFalse(overlays_path(root).exists())

    def test_supersession_keeps_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _convergent_fixture(root)
            first = self._accept_first_candidate(root)
            first_id = first["applied"]["overlay_id"]

            # Same convergence group re-accepted (e.g. regenerated statement):
            # build a second candidate manually with the same key.
            enqueue_out = enqueue_narrative_candidates(root)
            self.assertEqual(0, enqueue_out["enqueued"], "active overlay blocks auto re-emission")
            from core_memory.runtime.dreamer.candidates import _read_candidates, _write_candidates
            rows = _read_candidates(root)
            template = next(r for r in rows if r["hypothesis_type"] == "narrative_candidate")
            revised = {**template, "id": "dc-revised000001", "status": "pending",
                       "statement": "revised interpretation"}
            # A freshly generated candidate never carries an application marker.
            revised.pop("applied_overlay_id", None)
            rows.append(revised)
            _write_candidates(root, rows)

            second = decide_dreamer_candidate(
                root=root, candidate_id="dc-revised000001", decision="accept",
                reviewer="test", apply=True,
            )
            self.assertEqual(first_id, second["applied"]["supersedes_overlay_id"])

            active = read_active_overlays(root)
            self.assertEqual(1, len(active))
            self.assertEqual("revised interpretation", active[0]["statement"])
            # History retained: the storyline projection counts both versions.
            storylines = derive_storylines(root)["storylines"]
            with_overlay = [s for s in storylines if s["overlays"]]
            self.assertTrue(with_overlay)
            self.assertEqual(2, max(s["overlay_history_count"] for s in with_overlay))


class TestOneWayInvariant(unittest.TestCase):
    def test_backbone_identical_with_and_without_overlays(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _convergent_fixture(root)
            before = json.dumps(derive_worldlines(root), sort_keys=True)

            enqueue_narrative_candidates(root)
            pending = list_dreamer_candidates(root=root, status="pending")["results"]
            cid = [r for r in pending if r["hypothesis_type"] == "narrative_candidate"][0]["id"]
            decide_dreamer_candidate(root=root, candidate_id=cid, decision="accept", apply=True)
            self.assertTrue(overlays_path(root).exists())

            after = json.dumps(derive_worldlines(root), sort_keys=True)
            self.assertEqual(before, after, "overlays must never influence backbone derivation")

    def test_backbone_modules_never_read_overlays_file(self):
        repo = Path(__file__).resolve().parents[1]
        backbone_sources = [
            *(repo / "core_memory" / "claim").rglob("*.py"),
            *(repo / "core_memory" / "entity").rglob("*.py"),
            *(repo / "core_memory" / "association").rglob("*.py"),
            repo / "core_memory" / "graph" / "worldlines.py",
        ]
        for src in backbone_sources:
            text = src.read_text(encoding="utf-8")
            self.assertNotIn("overlays.jsonl", text, f"{src} must not read overlay records")
            self.assertNotIn("storyline_overlay", text, f"{src} must not consume overlays")


class TestStorylinesProjection(unittest.TestCase):
    def test_projection_joins_overlays_and_flags_tensions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _convergent_fixture(root)
            enqueue_narrative_candidates(root)
            pending = list_dreamer_candidates(root=root, status="pending")["results"]
            cid = [r for r in pending if r["hypothesis_type"] == "narrative_candidate"][0]["id"]
            decide_dreamer_candidate(root=root, candidate_id=cid, decision="accept", apply=True)

            out = derive_storylines(root)
            self.assertTrue(out["ok"])
            self.assertGreaterEqual(out["counts"]["with_overlays"], 1)
            covered = [s for s in out["storylines"] if s["overlays"]]
            for s in covered:
                self.assertIn(s["id"], covered[0]["overlays"][0]["supporting_worldline_ids"])
                self.assertEqual("active", s["overlays"][0]["status"])

    def test_empty_store(self):
        with tempfile.TemporaryDirectory() as td:
            out = derive_storylines(Path(td))
            self.assertTrue(out["ok"])
            self.assertEqual(0, out["total"])


class TestHttpStorylinesRoute(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("CORE_MEMORY_CANONICAL_SEMANTIC_MODE")
        os.environ["CORE_MEMORY_CANONICAL_SEMANTIC_MODE"] = "degraded_allowed"
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", None)
        else:
            os.environ["CORE_MEMORY_CANONICAL_SEMANTIC_MODE"] = self._old

    def test_storylines_endpoint(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _convergent_fixture(root)
            enqueue_narrative_candidates(root)
            pending = list_dreamer_candidates(root=root, status="pending")["results"]
            cid = [r for r in pending if r["hypothesis_type"] == "narrative_candidate"][0]["id"]
            decide_dreamer_candidate(root=root, candidate_id=cid, decision="accept", apply=True)

            c = TestClient(app)
            r = c.get("/v1/memory/projection/storylines", params={"root": str(root)})
            self.assertEqual(200, r.status_code)
            body = r.json()
            self.assertTrue(body["ok"])
            self.assertGreaterEqual(body["counts"]["with_overlays"], 1)
