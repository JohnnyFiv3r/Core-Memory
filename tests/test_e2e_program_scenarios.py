import json
import tempfile
import unittest
from pathlib import Path

from core_memory.memory_engine import (
    process_turn_finalized,
    process_flush,
    crawler_turn_context,
    apply_crawler_turn_updates,
    continuity_injection_context,
)
from core_memory.sidecar_worker import SidecarPolicy
from core_memory.tools.memory import execute


class TestE2EProgramScenarios(unittest.TestCase):
    def test_scenario_a_turn_to_flush_to_retrieval(self):
        with tempfile.TemporaryDirectory() as td:
            policy = SidecarPolicy(create_threshold=0.6)

            t1 = process_turn_finalized(
                root=td,
                session_id="sA",
                turn_id="t1",
                user_query="remember candidate-only promotion",
                assistant_final="Decision: candidate-only promotion prevents inflation.",
                policy=policy,
            )
            self.assertTrue(t1.get("ok"))

            t2 = process_turn_finalized(
                root=td,
                session_id="sA",
                turn_id="t2",
                user_query="remember archive-graph retrieval for durable memory",
                assistant_final="Decision: use archive graph for durable retrieval.",
                policy=policy,
            )
            self.assertTrue(t2.get("ok"))

            fl = process_flush(root=td, session_id="sA", promote=False, token_budget=1200, max_beads=80, source="flush_hook")
            self.assertTrue(fl.get("ok"))

            out = execute(
                {
                    "raw_query": "remember candidate-only promotion",
                    "intent": "remember",
                    "constraints": {"require_structural": False},
                    "k": 8,
                },
                root=td,
                explain=True,
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("answer", out.get("next_action"))
            self.assertGreater(len(out.get("results") or []), 0)

    def test_scenario_b_crawler_structured_append_only(self):
        with tempfile.TemporaryDirectory() as td:
            policy = SidecarPolicy(create_threshold=0.6)
            process_turn_finalized(
                root=td,
                session_id="sB",
                turn_id="t1",
                user_query="remember promotion workflow",
                assistant_final="Decision: narrow promotion usage.",
                policy=policy,
            )
            process_turn_finalized(
                root=td,
                session_id="sB",
                turn_id="t2",
                user_query="remember retrieval guardrails",
                assistant_final="Decision: keep contract-safe retrieval.",
                policy=policy,
            )

            ctx = crawler_turn_context(root=td, session_id="sB")
            beads = ctx.get("beads") or []
            self.assertGreaterEqual(len(beads), 2)
            b1 = beads[-1]["id"]
            b2 = beads[-2]["id"]

            upd = apply_crawler_turn_updates(
                root=td,
                session_id="sB",
                visible_bead_ids=ctx.get("visible_bead_ids") or [],
                updates={
                    "reviewed_beads": [
                        {
                            "bead_id": b1,
                            "promotion_state": "preserve_full_in_rolling",
                            "reason": "continuity",
                            "associations": [
                                {
                                    "target_bead_id": b2,
                                    "relationship": "supports",
                                    "confidence": 0.8,
                                }
                            ],
                        }
                    ]
                },
            )
            self.assertTrue(upd.get("ok"))
            self.assertEqual("session_side_log", upd.get("authority_path"))
            self.assertGreaterEqual(upd.get("promotions_marked", 0), 1)
            self.assertGreaterEqual(upd.get("associations_appended", 0), 1)

            queued_to = Path(str(upd.get("queued_to") or ""))
            self.assertTrue(queued_to.exists())
            queued_rows = [json.loads(line) for line in queued_to.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertGreaterEqual(len(queued_rows), 2)

            fl = process_flush(root=td, session_id="sB", promote=False, token_budget=900, max_beads=50, source="flush_hook")
            self.assertTrue(fl.get("ok"))
            self.assertEqual("flush_merge_projection", (fl.get("crawler_merge") or {}).get("authority_path"))
            self.assertGreaterEqual((fl.get("crawler_merge") or {}).get("promotions_marked", 0), 1)
            self.assertGreaterEqual((fl.get("crawler_merge") or {}).get("associations_appended", 0), 1)

            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertTrue((idx.get("beads", {}).get(b1) or {}).get("promotion_marked"))
            self.assertTrue(any(a.get("source_bead") == b1 and a.get("target_bead") == b2 for a in idx.get("associations", [])))
            self.assertEqual("", queued_to.read_text(encoding="utf-8"))

    def test_scenario_c_continuity_record_store_authority(self):
        with tempfile.TemporaryDirectory() as td:
            policy = SidecarPolicy(create_threshold=0.6)
            process_turn_finalized(
                root=td,
                session_id="sC",
                turn_id="t1",
                user_query="remember continuity item one",
                assistant_final="Outcome: continuity one captured.",
                policy=policy,
            )
            process_flush(root=td, session_id="sC", promote=False, token_budget=800, max_beads=40, source="flush_hook")

            inj = continuity_injection_context(workspace_root=td, max_items=20)
            self.assertEqual("rolling_record_store", inj.get("authority"))
            self.assertGreaterEqual(len(inj.get("records") or []), 1)
            self.assertTrue((Path(td) / "rolling-window.records.json").exists())


if __name__ == "__main__":
    unittest.main()
