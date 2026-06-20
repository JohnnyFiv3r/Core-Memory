from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class TestHttpMyelination(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_manifest_absent_then_present(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-myel-") as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)

            absent = c.get("/v1/myelination/manifest", params={"root": root})
            self.assertEqual(200, absent.status_code)
            self.assertFalse(absent.json()["present"])

            p = Path(root) / ".beads" / "events" / "myelination-manifest.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({
                "schema": "core_memory.myelination_manifest.v2",
                "enabled": True,
                "bonus_by_edge_key": {"a|supports|b": 0.2},
            }), encoding="utf-8")

            present = c.get("/v1/myelination/manifest", params={"root": root})
            self.assertEqual(200, present.status_code)
            body = present.json()
            self.assertTrue(body["present"])
            self.assertEqual(0.2, body["bonus_by_edge_key"]["a|supports|b"])

    def test_report_endpoint(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-myel-") as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            r = c.get("/v1/myelination/report", params={"root": root, "top": 5})
            self.assertEqual(200, r.status_code)
            self.assertEqual("core_memory.myelination_experiment.v1", r.json()["schema"])

    def test_reward_event_endpoint_writes_concrete_edge_event(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app
        from core_memory.runtime.observability.myelination_rewards import read_reward_events

        with tempfile.TemporaryDirectory(prefix="cm-http-myel-") as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            r = c.post(
                "/v1/myelination/reward-events",
                json={
                    "root": root,
                    "source_type": "overlay_decision",
                    "polarity": "positive",
                    "edge_keys": ["a|supports|b"],
                    "reward_tier": "validated_outcome",
                    "source_event_id": "surface-1",
                    "reason": "surface accepted association",
                },
            )
            self.assertEqual(200, r.status_code)
            body = r.json()
            self.assertTrue(body["ok"])
            self.assertEqual("validated_outcome", body["reward_tier"])

            rows = read_reward_events(root, since="")
            self.assertEqual(1, len(rows))
            self.assertEqual("overlay_decision", rows[0]["source_type"])
            self.assertEqual("validated_outcome", rows[0]["reward_tier"])

    def test_reward_event_endpoint_preserves_edge_only_guardrail(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-myel-") as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            r = c.post(
                "/v1/myelination/reward-events",
                json={
                    "root": root,
                    "source_type": "overlay_decision",
                    "polarity": "positive",
                    "edge_keys": [],
                    "reward_tier": "validated_outcome",
                },
            )
            self.assertEqual(400, r.status_code)
            self.assertEqual("no_concrete_edges", r.json()["skipped"])


if __name__ == "__main__":
    unittest.main()
