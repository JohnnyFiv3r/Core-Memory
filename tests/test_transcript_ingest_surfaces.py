from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.integrations.mcp.tools.ingest import ingest_handler
from core_memory.persistence.store import MemoryStore
from core_memory.transcript_ingest import ingest_transcript, ingest_turn_envelopes, normalize_transcript_payload


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
        self.assertEqual("unpaired_final_user_turn", out["warnings"][0]["code"])

    def test_normalizer_accepts_legacy_flush_policy_alias(self):
        out = normalize_transcript_payload(
            {
                "transcript_id": "flush-alias",
                "flush_policy": "flush",
                "turns": [
                    {"role": "user", "content": "Legacy callers used flush."},
                    {"role": "assistant", "content": "Treat it as end_only."},
                ],
            }
        )

        self.assertEqual("end_only", out["flush_policy"])

    def test_ingest_pairs_user_query_and_assistant_final_without_dropping_odd_turn(self):
        with tempfile.TemporaryDirectory() as td:
            seen: list[dict] = []

            def fake_process_turn_finalized(root: str, **env):
                seen.append(dict(env))
                return {"ok": True, "bead_ids": [f"b{len(seen)}"]}

            with patch("core_memory.transcript_ingest.process_turn_finalized", side_effect=fake_process_turn_finalized):
                out = ingest_transcript(
                    root=str(Path(td) / "store"),
                    transcript_id="pairing-ac4",
                    session_id="s-ac4",
                    turns=[
                        {"role": "user", "content": "Question one?"},
                        {"role": "assistant", "content": "Answer one."},
                        {"role": "user", "content": "Question two?"},
                        {"role": "assistant", "content": "Answer two."},
                        {"role": "user", "content": "Unpaired question?"},
                    ],
                )

        self.assertEqual(3, len(seen))
        self.assertEqual(["user", "assistant"], [t["role"] for t in seen[0]["turns"]])
        self.assertEqual("Question one?", seen[0]["turns"][0]["content"])
        self.assertEqual("Answer one.", seen[0]["turns"][1]["content"])
        self.assertEqual(["user", "assistant"], [t["role"] for t in seen[1]["turns"]])
        self.assertEqual(["user"], [t["role"] for t in seen[2]["turns"]])
        self.assertEqual("unpaired_final_user_turn", out["warnings"][0]["code"])

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
        self.assertIn("associations_created", out)

    def test_turn_envelope_ingest_populates_window_beads_per_turn(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(str(Path(td) / "store"))
            prior = store.add_bead(type="context", title="Prior", summary=["prior"], session_id="s1")
            envelopes = [
                {"session_id": "s1", "turn_id": "t1", "turns": [{"role": "user", "content": "one"}], "metadata": {}},
                {"session_id": "s1", "turn_id": "t2", "turns": [{"role": "user", "content": "two"}], "metadata": {}},
            ]

            def fake_process_turn_finalized(root: str, **env):
                title = f"bead-{env['turn_id']}"
                bid = MemoryStore(root).add_bead(type="context", title=title, summary=[title], session_id=env["session_id"], source_turn_ids=[env["turn_id"]])
                return {"ok": True, "bead_ids": [bid]}

            with patch("core_memory.transcript_ingest.process_turn_finalized", side_effect=fake_process_turn_finalized) as spy:
                out = ingest_turn_envelopes(root=str(Path(td) / "store"), envelopes=envelopes)

            first_window = spy.call_args_list[0].kwargs["window_bead_ids"]
            second_window = spy.call_args_list[1].kwargs["window_bead_ids"]

        self.assertTrue(out["ok"])
        self.assertIn(prior, first_window)
        self.assertIn(prior, second_window)
        self.assertIn(out["ingested"][0]["bead_ids"][0], second_window)

    def test_turn_envelope_ingest_preserves_singular_receipt_bead_id(self):
        envelopes = [
            {
                "session_id": "s1",
                "turn_id": "t1",
                "turns": [{"role": "user", "content": "remember this"}],
                "metadata": {},
            }
        ]
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.transcript_ingest.process_turn_finalized",
            return_value={
                "ok": True,
                "bead_id": "bead-committed",
                "semantic_status": "committed",
            },
        ):
            out = ingest_turn_envelopes(
                root=str(Path(td) / "store"),
                envelopes=envelopes,
            )

        self.assertTrue(out["ok"])
        self.assertEqual(["bead-committed"], out["ingested"][0]["bead_ids"])

    def test_turn_envelope_ingest_reports_pending_semantic_write_as_failure(self):
        envelopes = [
            {
                "session_id": "s1",
                "turn_id": "t1",
                "turns": [{"role": "user", "content": "remember this"}],
                "metadata": {},
            }
        ]
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.transcript_ingest.process_turn_finalized",
            return_value={
                "ok": False,
                "accepted": True,
                "retryable": True,
                "semantic_status": "pending",
                "error_code": "semantic_write_pending",
            },
        ):
            out = ingest_turn_envelopes(
                root=str(Path(td) / "store"),
                envelopes=envelopes,
            )

        self.assertFalse(out["ok"])
        self.assertEqual(0, out["turns_ingested"])
        self.assertEqual("failed", out["ingested"][0]["status"])
        self.assertEqual("semantic_write_pending", out["errors"][0]["error"])
        self.assertTrue(out["errors"][0]["retryable"])

    def test_transcript_result_summarizes_created_associations(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "store")

            def fake_process_turn_finalized(root: str, **env):
                store = MemoryStore(root)
                b1 = store.add_bead(type="decision", title="A", summary=["A"], session_id=env["session_id"], source_turn_ids=[env["turn_id"]])
                b2 = store.add_bead(type="context", title="B", summary=["B"], session_id=env["session_id"], source_turn_ids=[env["turn_id"]])
                idx = store._read_json(store.beads_dir / "index.json")
                idx.setdefault("associations", []).append(
                    {"source_bead": b1, "target_bead": b2, "relationship": "supports", "confidence": 0.8}
                )
                store._write_json(store.beads_dir / "index.json", idx)
                return {"ok": True, "bead_ids": [b1, b2]}

            with patch("core_memory.transcript_ingest.process_turn_finalized", side_effect=fake_process_turn_finalized):
                out = ingest_transcript(
                    root=root,
                    transcript_id="assoc-demo",
                    session_id="s1",
                    turns=[{"role": "user", "content": "A"}, {"role": "assistant", "content": "B"}],
                )

        self.assertEqual(1, out["associations_created"]["count"])
        self.assertEqual({"supports": 1}, out["associations_created"]["by_type"])
        self.assertEqual("supports", out["associations_created"]["items"][0]["relationship"])

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
