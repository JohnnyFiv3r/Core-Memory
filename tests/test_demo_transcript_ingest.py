from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path


class _JsonRequest:
    headers = {"content-type": "application/json"}

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _response_json(response):
    if isinstance(response, dict):
        return dict(response)
    body = getattr(response, "body", b"")
    if body:
        return json.loads(body.decode("utf-8"))
    return {}


def _response_status(response, default=200):
    return int(getattr(response, "status_code", default) or default)


class TestLocalDemoTranscriptIngest(unittest.TestCase):
    def setUp(self):
        try:
            import demo.app as demo_app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"demo stack unavailable: {exc}")

    def test_local_demo_ingest_transcript_returns_job_and_status(self):
        import demo.app as demo_app

        with tempfile.TemporaryDirectory() as td:
            old_root = demo_app.MEMORY_ROOT
            old_jobs = dict(demo_app.INGEST_JOBS)
            demo_app.MEMORY_ROOT = str(Path(td) / "memory")
            demo_app.INGEST_JOBS.clear()
            try:
                res = asyncio.run(
                    demo_app.ingest_transcript_endpoint(
                        _JsonRequest(
                            {
                                "transcript_id": "local-demo-parity",
                                "session_id": "local-demo-session",
                                "flush_policy": "none",
                                "turns": [
                                    {"role": "user", "content": "Project Ibis uses FoundationDB for ordering."},
                                    {"role": "assistant", "content": "Recorded."},
                                ],
                            }
                        )
                    )
                )
                self.assertEqual(202, _response_status(res))
                data = _response_json(res)
                self.assertTrue(data.get("ok"))
                self.assertEqual("transcript_ingest", data.get("kind"))
                self.assertEqual("queued", data.get("status"))
                self.assertEqual(2, data.get("turns_received"))
                self.assertEqual(1, data.get("turns_paired"))
                job_id = str(data.get("job_id") or "")
                self.assertTrue(job_id.startswith("ingest-"))

                last = None
                for _ in range(100):
                    status = asyncio.run(demo_app.ingest_job_status_endpoint(job_id))
                    self.assertEqual(200, _response_status(status))
                    last = _response_json(status)
                    if last.get("done"):
                        break
                    time.sleep(0.2)
                self.assertIsNotNone(last)
                self.assertTrue(last.get("done"), last)
                self.assertEqual("completed", last.get("status"), last)
                result = dict(last.get("result") or {})
                self.assertEqual("transcript_ingest", result.get("kind"))
                self.assertEqual("local-demo-parity", result.get("transcript_id"))
                self.assertEqual("local-demo-session", result.get("session_id"))
                self.assertEqual(1, result.get("turns_ingested"))
                self.assertTrue((Path(demo_app.MEMORY_ROOT) / ".beads" / "events" / "memory-events.jsonl").exists())
            finally:
                demo_app.MEMORY_ROOT = old_root
                demo_app.INGEST_JOBS.clear()
                demo_app.INGEST_JOBS.update(old_jobs)

    def test_local_demo_bad_ingest_payload_returns_422(self):
        import demo.app as demo_app

        res = asyncio.run(demo_app.ingest_transcript_endpoint(_JsonRequest({"turns": []})))
        self.assertEqual(422, _response_status(res))
        self.assertFalse(_response_json(res).get("ok"))
        self.assertIn("turns_required", str(_response_json(res)))


class TestTranscriptIngestCrossSurfaceParity(unittest.TestCase):
    def test_library_cli_mcp_and_local_demo_share_transcript_contract(self):
        from core_memory.integrations.api import list_turn_summaries
        from core_memory.integrations.mcp.tools.ingest import ingest_handler
        from core_memory.transcript_ingest import ingest_transcript
        import demo.app as demo_app
        import subprocess
        import sys

        turns = [
            {"role": "user", "content": "Project Ibis uses FoundationDB for ordering."},
            {"role": "assistant", "content": "Recorded."},
        ]
        surfaces: dict[str, dict] = {}
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            lib_root = str(base / "library")
            surfaces["library"] = ingest_transcript(root=lib_root, transcript_id="parity", session_id="parity-session", turns=turns)

            mcp_root = str(base / "mcp")
            surfaces["mcp"] = ingest_handler({"root": mcp_root, "transcript_id": "parity", "session_id": "parity-session", "turns": turns})["raw"]

            cli_root = str(base / "cli")
            transcript = base / "parity.json"
            transcript.write_text(json.dumps({"messages": turns}), encoding="utf-8")
            cli = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "core_memory.cli",
                    "--root",
                    cli_root,
                    "ingest",
                    "transcript",
                    str(transcript),
                    "--from",
                    "json",
                    "--transcript-id",
                    "parity",
                    "--session-id",
                    "parity-session",
                ],
                cwd=str(cwd),
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, cli.returncode, cli.stderr)
            surfaces["cli"] = json.loads(cli.stdout)["raw"]

            old_root = demo_app.MEMORY_ROOT
            old_jobs = dict(demo_app.INGEST_JOBS)
            demo_app.MEMORY_ROOT = str(base / "local-demo")
            demo_app.INGEST_JOBS.clear()
            try:
                created = asyncio.run(
                    demo_app.ingest_transcript_endpoint(
                        _JsonRequest({"transcript_id": "parity", "session_id": "parity-session", "flush_policy": "none", "turns": turns})
                    )
                )
                self.assertEqual(202, _response_status(created), created)
                job_id = _response_json(created)["job_id"]
                last = None
                for _ in range(100):
                    status = asyncio.run(demo_app.ingest_job_status_endpoint(job_id))
                    self.assertEqual(200, _response_status(status))
                    last = _response_json(status)
                    if last.get("done"):
                        break
                    time.sleep(0.2)
                self.assertTrue((last or {}).get("done"), last)
                surfaces["local_demo"] = dict((last or {}).get("result") or {})
            finally:
                demo_app.MEMORY_ROOT = old_root
                demo_app.INGEST_JOBS.clear()
                demo_app.INGEST_JOBS.update(old_jobs)

            for name, out in surfaces.items():
                self.assertTrue(out.get("ok"), (name, out))
                self.assertEqual("transcript_ingest", out.get("kind"), name)
                self.assertEqual("parity", out.get("transcript_id"), name)
                self.assertEqual("parity-session", out.get("session_id"), name)
                self.assertEqual(2, out.get("turns_received"), name)
                self.assertEqual(1, out.get("turns_paired"), name)
                self.assertEqual(1, out.get("turns_ingested"), name)

            for name, root in {
                "library": lib_root,
                "mcp": mcp_root,
                "cli": cli_root,
                "local_demo": str(base / "local-demo"),
            }.items():
                summaries = list_turn_summaries(root=root, session_id="parity-session", limit=5)
                rows = list(summaries.get("items") or summaries.get("turns") or summaries.get("results") or [])
                self.assertTrue(rows, (name, summaries))
                self.assertIn("transcript:parity:turn-0001", str(rows[0]))


if __name__ == "__main__":
    unittest.main()
