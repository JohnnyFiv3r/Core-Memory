import tempfile
import unittest
from pathlib import Path


class TestDemoObservabilityApi(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            import demo.app as demo_app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi/demo stack unavailable: {exc}")

    def test_demo_state_and_benchmark_endpoints(self):
        from fastapi.testclient import TestClient
        import demo.app as demo_app

        old_root = demo_app.MEMORY_ROOT
        old_coord = demo_app.COORDINATOR
        old_last_turn = dict(demo_app.LAST_TURN_DIAGNOSTICS or {})
        old_last_bench = dict(demo_app.LAST_BENCHMARK_REPORT or {})
        old_last_bench_summary = dict(demo_app.LAST_BENCHMARK_SUMMARY or {})
        old_last_bench_history = list(demo_app.LAST_BENCHMARK_HISTORY or [])
        old_last_flush = dict(demo_app.LAST_FLUSH_EVENT or {})
        old_last_flush_events = list(demo_app.LAST_FLUSH_EVENTS or [])

        with tempfile.TemporaryDirectory() as td:
            demo_app.MEMORY_ROOT = str(Path(td) / "memory")
            demo_app.COORDINATOR = None
            demo_app.LAST_TURN_DIAGNOSTICS = {}
            demo_app.LAST_BENCHMARK_REPORT = {}
            demo_app.LAST_BENCHMARK_SUMMARY = {}
            demo_app.LAST_BENCHMARK_HISTORY = []
            demo_app.LAST_FLUSH_EVENT = {}
            demo_app.LAST_FLUSH_EVENTS = []

            c = TestClient(demo_app.app)

            r1 = c.get("/api/demo/state")
            self.assertEqual(200, r1.status_code)
            s = r1.json()
            self.assertIn("session", s)
            self.assertIn("memory", s)
            self.assertIn("claims", s)
            self.assertIn("entities", s)
            self.assertIn("runtime", s)
            self.assertIn("flush_history", dict((s.get("runtime") or {})))
            self.assertIn("queue_breakdown", dict((s.get("runtime") or {})))

            as_of = "2026-01-01T00:00:00Z"
            r1b = c.get(f"/api/demo/state?as_of={as_of}")
            self.assertEqual(200, r1b.status_code)
            s2 = r1b.json()
            self.assertEqual(as_of, ((s2.get("claims") or {}).get("as_of")))

            r1c = c.get(f"/api/demo/claim-slot/demo/topic?as_of={as_of}")
            self.assertEqual(200, r1c.status_code)
            slot = r1c.json()
            self.assertTrue(slot.get("ok"))
            self.assertEqual(as_of, slot.get("as_of"))

            r2 = c.post(
                "/api/benchmark-run",
                json={
                    "subset": "local",
                    "limit": 1,
                    "root_mode": "clean",
                    "preload_from_demo": False,
                    "semantic_mode": "degraded_allowed",
                },
            )
            self.assertEqual(200, r2.status_code)
            b = r2.json()
            self.assertTrue(b.get("ok"))
            summary = dict(b.get("summary") or {})
            self.assertEqual("clean", str(summary.get("root_mode") or ""))
            self.assertTrue(bool(summary.get("run_id")))

            r2b = c.post(
                "/api/benchmark-run",
                json={
                    "subset": "local",
                    "limit": 1,
                    "root_mode": "clean",
                    "preload_from_demo": False,
                    "semantic_mode": "degraded_allowed",
                    "myelination": "compare",
                },
            )
            self.assertEqual(200, r2b.status_code)
            b2 = r2b.json()
            self.assertTrue(b2.get("ok"))
            self.assertIn("myelination_comparison", dict(b2.get("report") or {}))
            s2 = dict(b2.get("summary") or {})
            self.assertIn("myelination_compare", s2)
            self.assertTrue(bool(s2.get("run_id")))

            r3 = c.get("/api/demo/benchmark/last")
            self.assertEqual(200, r3.status_code)
            last = r3.json()
            self.assertIn("summary", last)
            self.assertIn("report", last)
            self.assertIn("history", last)

            r3h = c.get("/api/demo/benchmark/history?limit=5")
            self.assertEqual(200, r3h.status_code)
            hh = r3h.json()
            self.assertTrue(hh.get("ok"))
            self.assertTrue(isinstance(hh.get("history"), list))

            run_a = str((summary or {}).get("run_id") or "")
            run_b = str((s2 or {}).get("run_id") or "")
            if run_a and run_b and run_a != run_b:
                rc = c.get(f"/api/demo/benchmark/compare/{run_a}/{run_b}")
                self.assertEqual(200, rc.status_code)
                cmp = rc.json()
                self.assertTrue(cmp.get("ok"))
                self.assertIn("compare", cmp)

            r3b = c.get("/api/demo/entities")
            self.assertEqual(200, r3b.status_code)
            ent = r3b.json()
            self.assertTrue(ent.get("ok"))
            self.assertIn("entities", ent)

            r3c = c.post("/api/demo/entities/merge/suggest", json={"min_score": 0.86, "max_pairs": 10, "source": "test"})
            self.assertEqual(200, r3c.status_code)
            sug = r3c.json()
            self.assertIn("ok", sug)

            r3d = c.post(
                "/api/demo/entities/merge/decide",
                json={"proposal_id": "missing", "decision": "accept", "keep_entity_id": "entity-foo", "apply": True},
            )
            self.assertIn(r3d.status_code, {200, 400})
            self.assertFalse(bool((r3d.json() or {}).get("ok")))

            r4 = c.post("/api/flush")
            self.assertEqual(200, r4.status_code)
            r5 = c.get("/api/demo/runtime")
            self.assertEqual(200, r5.status_code)
            rt = dict((r5.json() or {}).get("runtime") or {})
            self.assertTrue(isinstance(rt.get("flush_history"), list))
            self.assertGreaterEqual(len(list(rt.get("flush_history") or [])), 1)

        demo_app.MEMORY_ROOT = old_root
        demo_app.COORDINATOR = old_coord
        demo_app.LAST_TURN_DIAGNOSTICS = old_last_turn
        demo_app.LAST_BENCHMARK_REPORT = old_last_bench
        demo_app.LAST_BENCHMARK_SUMMARY = old_last_bench_summary
        demo_app.LAST_BENCHMARK_HISTORY = old_last_bench_history
        demo_app.LAST_FLUSH_EVENT = old_last_flush
        demo_app.LAST_FLUSH_EVENTS = old_last_flush_events


if __name__ == "__main__":
    unittest.main()
