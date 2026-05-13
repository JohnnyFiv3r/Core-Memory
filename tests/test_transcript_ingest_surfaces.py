from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core_memory.integrations.mcp.tools.ingest import ingest_handler
from core_memory.transcript_ingest import ingest_transcript, normalize_transcript_payload


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "core_memory.cli", *args]
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


class TranscriptIngestSurfaceTests(unittest.TestCase):
    def test_normalizer_pairs_user_assistant_turns(self):
        out = normalize_transcript_payload(
            {
                "transcript_id": "pairing-demo",
                "turns": [
                    {"role": "user", "content": "Remember Project Ibis uses FoundationDB."},
                    {"role": "assistant", "content": "Recorded."},
                    {"role": "user", "content": "Also remember benchmarks need representative workloads."},
                ],
            }
        )
        self.assertTrue(out["ok"])
        self.assertEqual(3, out["turns_received"])
        self.assertEqual(2, out["turns_paired"])
        self.assertEqual("TRANSCRIPT_INGEST", out["envelopes"][0]["origin"])
        self.assertEqual(["user", "assistant"], [t["role"] for t in out["envelopes"][0]["turns"]])
        self.assertEqual(["user"], [t["role"] for t in out["envelopes"][1]["turns"]])

    def test_direct_library_ingests_transcript(self):
        with tempfile.TemporaryDirectory() as td:
            out = ingest_transcript(
                root=str(Path(td) / "store"),
                transcript_id="library-demo",
                session_id="library:demo",
                turns=[
                    {"role": "user", "content": "Remember that transcript ingest has a direct library helper."},
                    {"role": "assistant", "content": "Noted."},
                ],
            )
        self.assertTrue(out["ok"])
        self.assertEqual("transcript_ingest", out["kind"])
        self.assertEqual(2, out["turns_received"])
        self.assertEqual(1, out["turns_paired"])
        self.assertEqual(1, out["turns_ingested"])
        self.assertEqual("library:demo", out["session_id"])

    def test_mcp_ingest_accepts_inline_turns(self):
        with tempfile.TemporaryDirectory() as td:
            out = ingest_handler(
                {
                    "root": str(Path(td) / "store"),
                    "transcript_id": "mcp-inline",
                    "turns": [
                        {"role": "user", "content": "MCP ingest accepts inline transcript turns."},
                        {"role": "assistant", "content": "Confirmed."},
                    ],
                }
            )
        self.assertTrue(out["ok"])
        self.assertEqual("inline", out["format"])
        self.assertEqual(2, out["turns_ingested"])
        self.assertEqual(1, out["turns_paired"])
        self.assertEqual("ingest:mcp-inline", out["session_id"])

    def test_cli_ingest_transcript_file(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-transcript-cli-") as td:
            root = Path(td) / "memory"
            transcript = Path(td) / "chat.json"
            transcript.write_text(
                json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": "CLI transcript ingest uses canonical turn finalization."},
                            {"role": "assistant", "content": "Confirmed."},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            out = _run_cli(["--root", str(root), "ingest", "transcript", str(transcript), "--from", "json"], cwd)
        self.assertEqual(0, out.returncode, out.stderr)
        payload = json.loads(out.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual("json", payload["format"])
        self.assertEqual(2, payload["turns_ingested"])
        self.assertEqual(1, payload["turns_paired"])


if __name__ == "__main__":
    unittest.main()
