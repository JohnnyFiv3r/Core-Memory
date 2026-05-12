import tempfile
import unittest
from unittest.mock import patch

from core_memory.policy.bead_typing import classify_bead_type
from core_memory.policy.rationale import extract_causal_because, sanitize_because_for_turn
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import process_turn_finalized
from core_memory.runtime.worker import SidecarPolicy


class TestRationaleExtraction(unittest.TestCase):
    def test_does_not_echo_weak_speculation(self):
        self.assertEqual([], extract_causal_because("maybe we should use Redis"))

    def test_extracts_explicit_because_clause(self):
        out = extract_causal_because(
            "We chose PostgreSQL over MySQL because JSONB support and 2x better performance made it the clear winner."
        )
        self.assertEqual(["JSONB support and 2x better performance made it the clear winner"], out)

    def test_questions_are_not_rationale(self):
        self.assertEqual([], extract_causal_because("Why did we choose PostgreSQL?"))
        self.assertEqual("context", classify_bead_type("Why did we choose PostgreSQL?", ""))

    def test_sanitize_removes_raw_echo_but_keeps_real_reason(self):
        self.assertEqual(
            [],
            sanitize_because_for_turn(["maybe we should use Redis"], user_query="maybe we should use Redis"),
        )
        self.assertEqual(
            ["lower operational risk"],
            sanitize_because_for_turn(["lower operational risk"], user_query="We chose SQLite because lower operational risk"),
        )

    def test_default_turn_write_keeps_empty_because_without_causal_reason(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_BEAD_TYPE_ALLOW_FALLBACK": "1",
                "CORE_MEMORY_BECAUSE_EXTRACTOR_MODE": "heuristic",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "maybe we should use Redis"}, {"speaker": "assistant", "role": "assistant", "content": "Noted as a tentative idea."}],
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out.get("ok"))
            store = MemoryStore(td)
            idx = store._read_json(store.beads_dir / "index.json")
            bead = next(iter((idx.get("beads") or {}).values()))
            self.assertEqual([], bead.get("because"))
            self.assertNotEqual("maybe we should use Redis", " ".join(bead.get("because") or []))


if __name__ == "__main__":
    unittest.main()
