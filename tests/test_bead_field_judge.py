import tempfile
import unittest
from unittest.mock import patch

from core_memory.policy.bead_judge import judge_bead_fields
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import process_turn_finalized
from core_memory.runtime.queue.worker import SidecarPolicy


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
    "state_change": "decision_recorded",
    "validity": "current",
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
        self.assertEqual("decision_recorded", out["state_change"])
        self.assertEqual("current", out["validity"])
        self.assertEqual({"mode": "llm"}, out["judge"])

    def test_heuristic_fallback_authors_durable_fields(self):
        with patch("core_memory.policy.bead_judge._llm_judge_provider_neutral", return_value=None), patch(
            "core_memory.policy.bead_judge._llm_judge_anthropic", return_value=None
        ), patch("core_memory.policy.bead_judge._llm_judge_openai", return_value=None), patch.dict(
            "os.environ", {"CORE_MEMORY_BEAD_FIELD_JUDGE_MODE": "heuristic"}, clear=False
        ):
            out = judge_bead_fields("Remember Alice adopted Pixel.", "Recorded Alice adopted Pixel.")
        self.assertTrue(out.get("entities"))
        self.assertTrue(out.get("title"))
        self.assertTrue(out.get("summary"))
        self.assertEqual("heuristic", (out.get("judge") or {}).get("mode"))

    def test_turn_write_uses_field_judge_only_when_explicit_fallback_enabled(self):
        stale_agent_updates = {
            "beads_create": [
                {
                    "type": "context",
                    "title": "Use Redis for cache invalidation.",
                    "summary": ["Use Redis for cache invalidation."],
                    "entities": ["Redis"],
                    "because": ["Use Redis for cache invalidation."],
                    "source_turn_ids": ["t1"],
                    "detail": "Recorded.",
                    "tags": ["crawler_reviewed", "turn_finalized"],
                }
            ],
            "associations": [
                {
                    "source_bead_id": "__current_turn__",
                    "target_bead_id": "bead-prior",
                    "relationship": "supports",
                    "reason_text": "same-session continuity",
                    "confidence": 0.7,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.policy.bead_judge._llm_judge_anthropic", return_value=JUDGED
        ), patch("core_memory.policy.bead_judge._llm_judge_openai", return_value=None), patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_BEAD_FIELD_JUDGE_MODE": "auto",
                "CORE_MEMORY_BEAD_JUDGE_FALLBACK": "1",
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "warn",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "1",
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

        # Explicit fallback fills missing semantic fields only; it does not
        # overwrite fields the adapter already authored.
        self.assertEqual("context", bead.get("type"))
        self.assertEqual("Use Redis for cache invalidation.", bead.get("title"))
        self.assertEqual(["Use Redis for cache invalidation."], bead.get("summary"))
        self.assertEqual(["Use Redis for cache invalidation."], bead.get("because"))
        self.assertEqual(["Redis"], bead.get("entities"))
        self.assertIn("llm_judged", bead.get("tags") or [])

    def test_locomo_replay_preserves_request_scoped_crawler_fields_without_llm_judge(self):
        crawler_updates = {
            "beads_create": [
                {
                    "type": "context",
                    "title": "Alice adopted a rescue dog named Pixel.",
                    "summary": ["Alice adopted Pixel."],
                    "source_turn_ids": ["t-locomo-1"],
                    "detail": "Alice adopted a rescue dog named Pixel.",
                    "entities": ["Alice", "Pixel"],
                    "topics": ["pets"],
                    "retrieval_eligible": True,
                    "retrieval_title": "Alice adopted Pixel",
                    "retrieval_facts": ["Alice adopted a rescue dog named Pixel."],
                    "tags": ["crawler_reviewed", "turn_finalized", "locomo_replay"],
                }
            ],
            "associations": [
                {
                    "source_bead_id": "__current_turn__",
                    "target_bead_id": "bead-prior",
                    "relationship": "supports",
                    "reason_text": "same-session continuity",
                    "confidence": 0.7,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.runtime.engine.judge_bead_fields", side_effect=AssertionError("judge should not run")
        ), patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "warn",
                "CORE_MEMORY_AGENT_AUTHORED_SEMANTIC_GATE": "off",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="locomo:conv-26:session:1",
                turn_id="t-locomo-1",
                turns=[{"speaker": "alice", "role": "other", "content": "Alice adopted a rescue dog named Pixel."}],
                metadata={"replay_source": "locomo", "crawler_updates": crawler_updates},
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out.get("ok"))
            store = MemoryStore(td)
            idx = store._read_json(store.beads_dir / "index.json")
            bead = next(iter((idx.get("beads") or {}).values()))

        self.assertEqual("Alice adopted a rescue dog named Pixel.", bead.get("title"))
        self.assertEqual(["Alice adopted Pixel."], bead.get("summary"))
        # topics="pets" folds into entities at normalization time
        entities = bead.get("entities") or []
        self.assertIn("Alice", entities)
        self.assertIn("Pixel", entities)
        self.assertIn("locomo_replay", bead.get("tags") or [])
        self.assertNotIn("llm_judged", bead.get("tags") or [])

    def test_untrusted_replay_rows_preserve_authored_fields_without_default_judge(self):
        crawler_updates = {
            "beads_create": [
                {
                    "type": "context",
                    "title": "stale crawler title",
                    "summary": ["stale summary"],
                    "retrieval_title": "stale crawler title",
                    "retrieval_facts": ["stale summary"],
                    "retrieval_eligible": True,
                    "entities": ["Redis"],
                    "topics": ["cache"],
                    "source_turn_ids": ["t-locomo-2"],
                    "tags": ["crawler_reviewed", "turn_finalized"],
                }
            ],
            "associations": [
                {
                    "source_bead_id": "__current_turn__",
                    "target_bead_id": "bead-prior",
                    "relationship": "supports",
                    "reason_text": "same-session continuity",
                    "confidence": 0.7,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.policy.bead_judge._llm_judge_anthropic", return_value=JUDGED
        ), patch("core_memory.policy.bead_judge._llm_judge_openai", return_value=None), patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_BEAD_FIELD_JUDGE_MODE": "auto",
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "warn",
                "CORE_MEMORY_AGENT_AUTHORED_SEMANTIC_GATE": "off",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="locomo:conv-26:session:1",
                turn_id="t-locomo-2",
                turns=[{"speaker": "alice", "role": "other", "content": "Use Redis for cache invalidation."}],
                metadata={"replay_source": "locomo", "crawler_updates": crawler_updates},
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out.get("ok"))
            store = MemoryStore(td)
            idx = store._read_json(store.beads_dir / "index.json")
            bead = next(iter((idx.get("beads") or {}).values()))

        self.assertEqual("stale crawler title", bead.get("title"))
        self.assertNotIn("llm_judged", bead.get("tags") or [])
        self.assertNotIn("agent_authored_semantic", bead.get("tags") or [])


if __name__ == "__main__":
    unittest.main()
