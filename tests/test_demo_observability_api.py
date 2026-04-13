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
        old_last_flush = dict(demo_app.LAST_FLUSH_EVENT or {})

        with tempfile.TemporaryDirectory() as td:
            demo_app.MEMORY_ROOT = str(Path(td) / "memory")
            demo_app.COORDINATOR = None
            demo_app.LAST_TURN_DIAGNOSTICS = {}
            demo_app.LAST_BENCHMARK_REPORT = {}
            demo_app.LAST_BENCHMARK_SUMMARY = {}
            demo_app.LAST_FLUSH_EVENT = {}

            c = TestClient(demo_app.app)

            r1 = c.get("/api/demo/state")
            self.assertEqual(200, r1.status_code)
            s = r1.json()
            self.assertIn("session", s)
            self.assertIn("memory", s)
            self.assertIn("claims", s)
            self.assertIn("runtime", s)

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

            r3 = c.get("/api/demo/benchmark/last")
            self.assertEqual(200, r3.status_code)
            last = r3.json()
            self.assertIn("summary", last)
            self.assertIn("report", last)

        demo_app.MEMORY_ROOT = old_root
        demo_app.COORDINATOR = old_coord
        demo_app.LAST_TURN_DIAGNOSTICS = old_last_turn
        demo_app.LAST_BENCHMARK_REPORT = old_last_bench
        demo_app.LAST_BENCHMARK_SUMMARY = old_last_bench_summary
        demo_app.LAST_FLUSH_EVENT = old_last_flush


if __name__ == "__main__":
    unittest.main()
