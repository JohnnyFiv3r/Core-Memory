from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_memory.graph.junctions import (
    JUNCTION_SCHEMA,
    derive_junction_projection,
    resolve_junction_matches,
)


def _bead(
    title: str,
    created_at: str,
    *,
    entities: list[str] | None = None,
    claims: list[dict] | None = None,
    type_: str = "context",
) -> dict:
    return {
        "type": type_,
        "title": title,
        "summary": [title],
        "created_at": created_at,
        "status": "open",
        "retrieval_eligible": True,
        "entities": list(entities or []),
        "claims": list(claims or []),
    }


def _claim(claim_id: str, subject: str, slot: str, value: str) -> dict:
    return {
        "id": claim_id,
        "claim_kind": "state",
        "subject": subject,
        "slot": slot,
        "value": value,
        "reason_text": "Observed in source evidence.",
        "confidence": 0.9,
    }


def _write_index(root: Path, beads: dict[str, dict]) -> None:
    beads_dir = root / ".beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    (beads_dir / "index.json").write_text(
        json.dumps({"beads": beads, "associations": []}),
        encoding="utf-8",
    )


class TestJunctionProjection(unittest.TestCase):
    def test_claim_identity_is_primary_and_emits_corroboration(self):
        with tempfile.TemporaryDirectory(prefix="cm-junction-") as td:
            root = Path(td)
            beads = {
                "b1": _bead(
                    "Warehouse plan",
                    "2026-01-01T00:00:00Z",
                    claims=[_claim("c1", "Acme", "warehouse strategy", "centralize")],
                ),
                "b2": _bead(
                    "Warehouse update",
                    "2026-02-01T00:00:00Z",
                    claims=[_claim("c2", "Acme", "warehouse strategy", "pilot")],
                ),
                "b3": _bead("Unrelated", "2026-03-01T00:00:00Z"),
            }
            _write_index(root, beads)

            projection = derive_junction_projection(root, embeddings={})
            claim_identity = next(row for row in projection["identities"] if row["tier"] == "claim_slot")
            self.assertEqual(["b1", "b2"], claim_identity["bead_ids"])
            self.assertEqual(1, claim_identity["corroboration_count"])
            bead_rows = {row["bead_id"]: row for row in projection["beads"]}
            self.assertEqual(1, bead_rows["b1"]["corroboration_count"])
            self.assertEqual(["b2"], bead_rows["b1"]["corroborating_bead_ids"])
            self.assertTrue(projection["density"]["stitching_ready"])
            self.assertEqual(
                2.0,
                projection["density"]["identity_junction_set_size_distribution"]["p50"],
            )

            match = resolve_junction_matches(root, "b1", "b2", projection=projection)
            self.assertTrue(match["same_place"])
            self.assertEqual("claim_slot", match["best_match"]["tier"])

    def test_embedding_radius_moves_with_backbone_adjacency_distribution(self):
        with tempfile.TemporaryDirectory(prefix="cm-junction-") as td:
            root = Path(td)
            beads = {
                "b1": _bead("First", "2026-01-01T00:00:00Z", entities=["Acme Corp"]),
                "b2": _bead("Second", "2026-02-01T00:00:00Z", entities=["Acme Corp"]),
            }
            _write_index(root, beads)
            first = derive_junction_projection(
                root,
                embeddings={"b1": [1.0, 0.0], "b2": [0.8, 0.6]},
            )

            beads["b3"] = _bead("Third", "2026-03-01T00:00:00Z", entities=["Acme Corp"])
            _write_index(root, beads)
            second = derive_junction_projection(
                root,
                embeddings={"b1": [1.0, 0.0], "b2": [0.8, 0.6], "b3": [0.0, 1.0]},
            )

            self.assertEqual("calibrated_from_backbone_adjacency", first["metadata"]["d_p_source"])
            self.assertEqual(1, first["metadata"]["adjacent_pair_count"])
            self.assertEqual(2, second["metadata"]["adjacent_pair_count"])
            self.assertNotEqual(first["metadata"]["d_p"], second["metadata"]["d_p"])

    def test_embedding_is_fallback_after_structural_identities(self):
        with tempfile.TemporaryDirectory(prefix="cm-junction-") as td:
            root = Path(td)
            beads = {
                "a1": _bead("A1", "2026-01-01T00:00:00Z", entities=["Calibration Thread"]),
                "a2": _bead("A2", "2026-02-01T00:00:00Z", entities=["Calibration Thread"]),
                "a3": _bead("A3", "2026-03-01T00:00:00Z", entities=["Calibration Thread"]),
                "x": _bead("X", "2026-01-10T00:00:00Z"),
                "y": _bead("Y", "2026-01-11T00:00:00Z"),
            }
            _write_index(root, beads)
            projection = derive_junction_projection(
                root,
                embeddings={
                    "a1": [1.0, 0.0],
                    "a2": [0.8, 0.6],
                    "a3": [-1.0, 0.0],
                    "x": [1.0, 0.0],
                    "y": [0.99, 0.01],
                },
            )
            match = resolve_junction_matches(root, "x", "y", projection=projection)
            self.assertTrue(match["same_place"])
            self.assertEqual("embedding", match["best_match"]["tier"])

    def test_entity_bounds_are_derived_and_filter_a_support_outlier(self):
        with tempfile.TemporaryDirectory(prefix="cm-junction-") as td:
            root = Path(td)
            beads: dict[str, dict] = {}
            for index in range(8):
                entities = ["Workspace Everywhere"]
                if index < 2:
                    entities.append("Entity Alpha")
                if 2 <= index < 4:
                    entities.append("Entity Beta")
                if 4 <= index < 6:
                    entities.append("Entity Gamma")
                beads[f"b{index}"] = _bead(
                    f"Bead {index}",
                    f"2026-01-{index + 1:02d}T00:00:00Z",
                    entities=entities,
                )
            _write_index(root, beads)

            projection = derive_junction_projection(root, embeddings={})
            labels = {row["label"] for row in projection["identities"] if row["tier"] == "entity_worldline"}
            bounds = projection["metadata"]["entity_support"]
            self.assertEqual(2, bounds["support_floor"])
            self.assertLess(bounds["ubiquity_support_cap"], 8)
            self.assertNotIn("Workspace Everywhere", labels)
            self.assertTrue({"Entity Alpha", "Entity Beta", "Entity Gamma"} <= labels)

    def test_entity_bounds_ignore_non_curated_labels(self):
        with tempfile.TemporaryDirectory(prefix="cm-junction-") as td:
            root = Path(td)
            beads: dict[str, dict] = {}
            for index in range(8):
                entities = ["tests"]
                if index < 2:
                    entities.append("Entity Alpha")
                if 2 <= index < 4:
                    entities.append("Entity Beta")
                if 4 <= index < 6:
                    entities.append("Entity Gamma")
                beads[f"b{index}"] = _bead(
                    f"Bead {index}",
                    f"2026-02-{index + 1:02d}T00:00:00Z",
                    entities=entities,
                )
            _write_index(root, beads)

            projection = derive_junction_projection(root, embeddings={})
            bounds = projection["metadata"]["entity_support"]
            labels = {row["label"] for row in projection["identities"]}
            self.assertEqual(3, bounds["sample_count"])
            self.assertEqual(2, bounds["support_floor"])
            self.assertNotIn("tests", labels)

    def test_sparse_projection_fails_density_gate_explicitly(self):
        with tempfile.TemporaryDirectory(prefix="cm-junction-") as td:
            root = Path(td)
            _write_index(
                root,
                {
                    "goal-1": _bead(
                        "Launch beta",
                        "2026-01-01T00:00:00Z",
                        type_="goal",
                    )
                },
            )
            projection = derive_junction_projection(root, embeddings={})
            self.assertFalse(projection["density"]["stitching_ready"])
            self.assertEqual("most_junction_identities_are_empty", projection["density"]["gate_reason"])
            self.assertIn("junction_density_gate_failed_defer_per_search", projection["limitations"])


class TestHttpJunctionProjection(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401

            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_projection_endpoint_exposes_density_without_bead_rows(self):
        from fastapi.testclient import TestClient

        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-junction-") as td:
            root = Path(td)
            _write_index(
                root,
                {
                    "b1": _bead(
                        "First",
                        "2026-01-01T00:00:00Z",
                        claims=[_claim("c1", "Acme", "margin", "10")],
                    ),
                    "b2": _bead(
                        "Second",
                        "2026-02-01T00:00:00Z",
                        claims=[_claim("c2", "Acme", "margin", "12")],
                    ),
                },
            )
            response = TestClient(app).get(
                "/v1/memory/projection/junctions",
                params={"root": str(root), "include_beads": False},
            )
            self.assertEqual(200, response.status_code)
            payload = response.json()
            self.assertEqual(JUNCTION_SCHEMA, payload["schema_version"])
            self.assertTrue(payload["density"]["stitching_ready"])
            self.assertNotIn("beads", payload)


if __name__ == "__main__":
    unittest.main()
