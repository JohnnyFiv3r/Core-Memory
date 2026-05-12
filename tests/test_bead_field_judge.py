import tempfile
import unittest
from unittest.mock import patch

from core_memory.policy.bead_judge import judge_bead_fields
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import process_turn_finalized
from core_memory.runtime.worker import SidecarPolicy


JUDGED = {
    "type": "decision",
    "title": "Use Redis for cache invalidation",
    "summary": ["Redis was selected for cache invalidation."],
    "detail": "The turn records a decision to use Redis where cache invalidation is the purpose.",
    "because": [
        {
            "text": "cache invalidation requires a shared low-latency coordination point",
            "category": "purpose",
            "source_span": "for cache invalidation",
            "confidence": 0.82,
            "stated": "inferred",
        }
    ],
    "supporting_facts": ["The user stated Redis should be used for cache invalidation."],
    "evidence_refs": [],
    "entities": ["Redis"],
    "topics": ["cache invalidation"],
    "state_change": "decision_recorded",
    "validity": "current",
    "retrieval_eligible": True,
    "retrieval_title": "Redis cache invalidation decision",
    "retrieval_facts": ["Redis is the selected coordination point for cache invalidation."],
    "effective_from": "",
    "effective_to": "",
    "observed_at": "",
}


class TestBeadFieldJudge(unittest.TestCase):
    def test_llm_judge_authors_every_semantic_field(self):
        with patch("core_memory.policy.bead_judge._llm_judge_anthropic", return_value=JUDGED), patch(
            "core_memory.policy.bead_judge._llm_judge_openai", return_value=None
        ), patch.dict("os.environ", {"CORE_MEMORY_BEAD_FIELD_JUDGE_MODE": "auto"}, clear=False):
            out = judge_bead_fields("Use Redis for cache invalidation.", "Recorded.")

        self.assertEqual("decision", out["type"])
        self.assertEqual("Use Redis for cache invalidation", out["title"])
        self.assertEqual(["Redis was selected for cache invalidation."], out["summary"])
        self.assertEqual(["cache invalidation requires a shared low-latency coordination point"], out["because"])
        self.assertEqual(["The user stated Redis should be used for cache invalidation."], out["supporting_facts"])
        self.assertEqual(["Redis"], out["entities"])
        self.assertEqual(["cache invalidation"], out["topics"])
        self.assertEqual("decision_recorded", out["state_change"])
        self.assertEqual("current", out["validity"])
        self.assertTrue(out["retrieval_eligible"])
        self.assertEqual("Redis cache invalidation decision", out["retrieval_title"])
        self.assertEqual(["Redis is the selected coordination point for cache invalidation."], out["retrieval_facts"])
        self.assertEqual({"mode": "llm"}, out["judge"])

    def test_turn_write_uses_field_judge_over_raw_defaults_and_agent_row(self):
        stale_agent_updates = {
            "beads_create": [
                {
                    "type": "context",
                    "title": "Use Redis for cache invalidation.",
                    "summary": ["Use Redis for cache invalidation."],
                    "because": ["Use Redis for cache invalidation."],
                    "source_turn_ids": ["t1"],
                    "detail": "Recorded.",
                    "entities": [],
                    "tags": ["crawler_reviewed", "turn_finalized"],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.policy.bead_judge._llm_judge_anthropic", return_value=JUDGED
        ), patch("core_memory.policy.bead_judge._llm_judge_openai", return_value=None), patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_BEAD_FIELD_JUDGE_MODE": "auto",
                "CORE_MEMORY_AGENT_AUTHORED_SEMANTIC_GATE": "off",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "Use Redis for cache invalidation."}, {"speaker": "assistant", "role": "assistant", "content": "Recorded."}],
                metadata={"crawler_updates": stale_agent_updates},
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out.get("ok"))
            store = MemoryStore(td)
            idx = store._read_json(store.beads_dir / "index.json")
            bead = next(iter((idx.get("beads") or {}).values()))

        self.assertEqual("decision", bead.get("type"))
        self.assertEqual("Use Redis for cache invalidation", bead.get("title"))
        self.assertEqual(["cache invalidation requires a shared low-latency coordination point"], bead.get("because"))
        self.assertEqual(["Redis"], bead.get("entities"))
        self.assertTrue(bead.get("retrieval_eligible"))
        self.assertEqual("Redis cache invalidation decision", bead.get("retrieval_title"))
        self.assertEqual(["Redis is the selected coordination point for cache invalidation."], bead.get("retrieval_facts"))
        self.assertIn("llm_judged", bead.get("tags") or [])
        self.assertNotEqual(["Use Redis for cache invalidation."], bead.get("summary"))


if __name__ == "__main__":
    unittest.main()
