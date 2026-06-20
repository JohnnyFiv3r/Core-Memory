from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TestHttpSoulEndpointsComplete(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_apply_update_auto_governance(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app
        from core_memory.soul.store import propose_soul_update

        with tempfile.TemporaryDirectory(prefix="cm-http-soul-apply-") as td:
            root = str(Path(td) / "memory")
            p = propose_soul_update(root, target_file="SOUL.md", entry_key="s", content="inferred",
                                    source="agent", epistemic_status="inferred", requires_approval=True)
            c = TestClient(app)
            r = c.post("/v1/soul/apply-update", json={"root": root, "revision_id": p["revision_id"]})
            self.assertEqual(200, r.status_code)
            self.assertEqual("applied", r.json()["status"])

            endorsed = propose_soul_update(root, target_file="IDENTITY.md", entry_key="e",
                                           content="endorsed", source="agent",
                                           epistemic_status="endorsed", requires_approval=True)
            blocked = c.post("/v1/soul/apply-update", json={"root": root, "revision_id": endorsed["revision_id"]})
            self.assertEqual(400, blocked.status_code)
            self.assertEqual("requires_human_approval", blocked.json()["error"])

    def test_dreamer_endpoints_and_goals_list(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app
        from core_memory.runtime.dreamer.candidates import _write_candidates

        with tempfile.TemporaryDirectory(prefix="cm-http-soul-dreamer-") as td:
            root = str(Path(td) / "memory")
            _write_candidates(root, [{"id": "dc-1", "status": "pending",
                                      "hypothesis_type": "tension_candidate", "tension_key": "k1",
                                      "statement": "Goals conflict: k1."}])
            c = TestClient(app)

            findings = c.post("/v1/soul/dreamer/findings", json={"root": root})
            self.assertEqual(200, findings.status_code)
            self.assertEqual(1, findings.json()["count"])

            proposed = c.post("/v1/soul/dreamer/propose-updates", json={"root": root})
            self.assertEqual(1, proposed.json()["proposed"])

            review = c.post("/v1/soul/dreamer/run-review", json={"root": root})
            self.assertEqual(1, review.json()["count"])

            # Goals list endpoint.
            c.post("/v1/soul/goals/propose", json={"root": root, "title": "G", "goal_id": "g1"})
            goals = c.get("/v1/soul/goals", params={"root": root})
            self.assertEqual(200, goals.status_code)
            self.assertEqual(1, goals.json()["count"])

    def test_soul_file_entries_endpoint_returns_current_provenance(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app
        from core_memory.soul.store import propose_soul_update

        with tempfile.TemporaryDirectory(prefix="cm-http-soul-entries-") as td:
            root = str(Path(td) / "memory")
            propose_soul_update(
                root,
                target_file="IDENTITY.md",
                entry_key="operator-loop",
                content="Favors explicit review loops before durable edits.",
                source="agent",
                epistemic_status="inferred",
                reason="Observed settings workflow.",
                evidence=[{"kind": "ui", "ref": "settings:self-model"}],
                metadata={"confidence": 0.82},
                requires_approval=False,
            )
            c = TestClient(app)

            r = c.get(
                "/v1/soul/files/IDENTITY.md/entries",
                params={"root": root},
            )
            self.assertEqual(200, r.status_code)
            body = r.json()
            self.assertEqual("soul.entries.v1", body["contract"])
            self.assertEqual("IDENTITY.md", body["file_name"])
            self.assertIn("operator-loop", body["entries"])
            entry = body["entries"]["operator-loop"]
            self.assertEqual("inferred", entry["epistemic_status"])
            self.assertEqual("agent", entry["source"])
            self.assertEqual("Observed settings workflow.", entry["reason"])
            self.assertEqual([{"kind": "ui", "ref": "settings:self-model"}], entry["evidence"])
            self.assertEqual({"confidence": 0.82}, entry["metadata"])

            invalid = c.get(
                "/v1/soul/files/NOTES.md/entries",
                params={"root": root},
            )
            self.assertEqual(400, invalid.status_code)
            self.assertEqual("invalid_target_file", invalid.json()["error"])


if __name__ == "__main__":
    unittest.main()
