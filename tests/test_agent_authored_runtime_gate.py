from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import process_turn_finalized
from core_memory.schema.agent_authored_updates import AGENT_AUTHORED_UPDATES_V1


def _v1(rows, associations=None):
    return {
        "schema_version": AGENT_AUTHORED_UPDATES_V1,
        "beads_create": list(rows),
        "associations": list(associations or []),
        "reviewed_beads": [],
    }


class TestAgentAuthoredRuntimeGateSlice1(unittest.TestCase):
    def test_strict_mode_blocks_when_updates_missing(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "0",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "q"}, {"speaker": "assistant", "role": "assistant", "content": "a"}],
                metadata={},
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual("agent_callable_missing", out.get("error_code"))
            self.assertEqual("agent_callable_missing", (out.get("agent_contract_error") or {}).get("code"))
            gate = (out.get("crawler_handoff") or {}).get("agent_authored_gate") or {}
            self.assertTrue(gate.get("required"))
            self.assertTrue(gate.get("blocked"))

            # Never-forget is the raw turn event plus pending semantic state;
            # hard mode must not fabricate a context bead.
            self.assertTrue(out.get("gate_blocked"))
            self.assertFalse(out.get("bead_id"))
            idx = MemoryStore(td)._read_json(Path(td) / ".beads" / "index.json")
            self.assertEqual({}, idx.get("beads") or {})

    def test_strict_mode_blocks_when_updates_invalid(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "0",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "q"}, {"speaker": "assistant", "role": "assistant", "content": "a"}],
                metadata={"crawler_updates": _v1([{"title": "only title"}])},
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual("agent_updates_invalid", out.get("error_code"))
            self.assertEqual("agent_updates_invalid", (out.get("agent_contract_error") or {}).get("code"))

    def test_required_flag_rejects_even_when_legacy_fail_open_is_set(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "1",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "remember this"}, {"speaker": "assistant", "role": "assistant", "content": "decision text"}],
                metadata={},
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual("agent_callable_missing", out.get("error_code"))
            gate = (out.get("crawler_handoff") or {}).get("agent_authored_gate") or {}
            self.assertFalse(gate.get("used_fallback"))
            self.assertTrue(gate.get("blocked"))

    def test_strict_mode_accepts_valid_agent_updates(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "0",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            src_id = s.add_bead(type="context", title="src", summary=["s"], session_id="s1", source_turn_ids=["seed-1"])
            target_id = s.add_bead(type="context", title="target", summary=["t"], session_id="s1", source_turn_ids=["seed-2"])

            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "q"}, {"speaker": "assistant", "role": "assistant", "content": "a"}],
                metadata={
                    "crawler_updates": _v1(
                        [
                            {
                                "creation_role": "current_turn",
                                "type": "decision",
                                "title": "Agent decided",
                                "summary": ["summary"],
                                "because": ["agent-provided rationale"],
                                "retrieval_title": "Agent decided",
                                "retrieval_facts": ["summary"],
                                "retrieval_eligible": True,
                                "entities": ["Agent"],
                                "topics": ["decision"],
                                "source_turn_ids": ["t1"],
                            }
                        ],
                        [
                            {
                                "source_bead_id": src_id,
                                "target_bead_id": target_id,
                                "relationship": "supports",
                                "reason_text": "src supports target",
                                "confidence": 0.6,
                            }
                        ],
                    )
                },
            )
            self.assertTrue(out.get("ok"))
            gate = (out.get("crawler_handoff") or {}).get("agent_authored_gate") or {}
            self.assertEqual("metadata.crawler_updates", gate.get("source"))
            self.assertFalse(gate.get("used_fallback"))

    def test_strict_mode_blocks_when_bead_fields_missing(self):
        # Zero associations is now valid; hard mode blocks on missing required bead fields.
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "0",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "q"}, {"speaker": "assistant", "role": "assistant", "content": "a"}],
                metadata={
                    "crawler_updates": _v1([{
                        "creation_role": "current_turn",
                        "type": "decision",
                        "summary": ["summary"],
                        "entities": [],
                        "retrieval_eligible": False,
                        "source_turn_ids": ["t1"],
                    }])
                },
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual("agent_updates_invalid", out.get("error_code"))

    def test_strict_mode_allows_missing_associations_on_first_turn(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "0",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "q"}, {"speaker": "assistant", "role": "assistant", "content": "a"}],
                metadata={
                    "crawler_updates": _v1(
                        [
                            {
                                "creation_role": "current_turn",
                                "type": "decision",
                                "title": "Agent decided",
                                "summary": ["summary"],
                                "because": ["agent-provided rationale"],
                                "retrieval_title": "Agent decided",
                                "retrieval_facts": ["summary"],
                                "retrieval_eligible": True,
                                "entities": ["Agent"],
                                "topics": ["decision"],
                                "source_turn_ids": ["t1"],
                            }
                        ]
                    )
                },
            )
            self.assertTrue(out.get("ok"))
            gate = (out.get("crawler_handoff") or {}).get("agent_authored_gate") or {}
            self.assertFalse(gate.get("used_fallback"))

    def test_strict_mode_accepts_agent_callable_updates(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "0",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            src_id = s.add_bead(type="context", title="src", summary=["s"], session_id="s1", source_turn_ids=["seed-1"])
            target_id = s.add_bead(type="context", title="target", summary=["t"], session_id="s1", source_turn_ids=["seed-2"])

            invoked = _v1(
                [
                    {
                        "creation_role": "current_turn",
                        "type": "decision",
                        "title": "Agent callable row",
                        "summary": ["summary"],
                        "because": ["agent-provided rationale"],
                        "retrieval_title": "Agent callable row",
                        "retrieval_facts": ["summary"],
                        "retrieval_eligible": True,
                        "entities": ["Agent"],
                        "topics": ["decision"],
                        "source_turn_ids": ["t1"],
                    }
                ],
                [
                    {
                        "source_bead_id": src_id,
                        "target_bead_id": target_id,
                        "relationship": "supports",
                        "reason_text": "callable provided",
                        "confidence": 0.8,
                    }
                ],
            )

            with patch(
                "core_memory.runtime.engine.invoke_turn_crawler_agent",
                return_value=(invoked, {"attempted": True, "ok": True, "source": "agent_callable", "attempts": 1}),
            ):
                out = process_turn_finalized(
                    root=td,
                    session_id="s1",
                    turn_id="t1",
                    turns=[{"speaker": "user", "role": "user", "content": "q"}, {"speaker": "assistant", "role": "assistant", "content": "a"}],
                    metadata={},
                )

            self.assertTrue(out.get("ok"))
            gate = (out.get("crawler_handoff") or {}).get("agent_authored_gate") or {}
            self.assertEqual("agent_callable", gate.get("source"))
            self.assertFalse(gate.get("used_fallback"))

    def test_strict_mode_blocks_on_invocation_exhaustion(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "0",
            },
            clear=False,
        ), patch(
            "core_memory.runtime.engine.invoke_turn_crawler_agent",
            return_value=(None, {"attempted": True, "ok": False, "error_code": "agent_invocation_exhausted", "attempts": 2}),
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "q"}, {"speaker": "assistant", "role": "assistant", "content": "a"}],
                metadata={},
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual("agent_invocation_exhausted", out.get("error_code"))
            # The raw turn remains durable while semantics wait for a valid
            # authored retry.
            self.assertTrue(out.get("gate_blocked"))
            self.assertFalse(out.get("bead_id"))
            idx = MemoryStore(td)._read_json(Path(td) / ".beads" / "index.json")
            self.assertEqual({}, idx.get("beads") or {})

    def test_temporal_only_associations_warn_and_flag_committed_authored_bead(self):
        # Never-forget: semantic coverage violations no longer hard-block.
        # The bead is written, gate warns, and the coverage gap is flagged.
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "0",
                "CORE_MEMORY_AGENT_MIN_SEMANTIC_ASSOC_AFTER_FIRST": "1",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            src_id = s.add_bead(type="context", title="src", summary=["s"], session_id="s1", source_turn_ids=["seed-1"])
            target_id = s.add_bead(type="context", title="target", summary=["t"], session_id="s1", source_turn_ids=["seed-2"])

            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "q"}, {"speaker": "assistant", "role": "assistant", "content": "a"}],
                metadata={
                    "crawler_updates": _v1(
                        [
                            {
                                "creation_role": "current_turn",
                                "type": "decision",
                                "title": "Agent decided",
                                "summary": ["summary"],
                                "because": ["agent-provided rationale"],
                                "retrieval_title": "Agent decided",
                                "retrieval_facts": ["summary"],
                                "retrieval_eligible": True,
                                "entities": ["Agent"],
                                "topics": ["decision"],
                                "source_turn_ids": ["t1"],
                            }
                        ],
                        [
                            {
                                "source_bead_id": src_id,
                                "target_bead_id": target_id,
                                "relationship": "follows",
                                "reason_text": "temporal only",
                                "confidence": 0.7,
                            }
                        ],
                    )
                },
            )
            # Bead is written despite coverage gap (never-forget)
            self.assertTrue(out.get("ok"))
            self.assertFalse(out.get("gate_blocked"))
            self.assertFalse(out.get("error_code"))  # no top-level block error
            # Gate records the warning and coverage error code for diagnosis
            gate = (out.get("crawler_handoff") or {}).get("agent_authored_gate") or {}
            self.assertTrue(gate.get("warned"))
            self.assertEqual("agent_semantic_coverage_missing", gate.get("error_code"))
            # F-W2: the coverage flag must actually land on the canonical bead.
            self.assertTrue(out.get("bead_id"), "canonical turn bead id must be resolved")
            idx = MemoryStore(td)._read_json(Path(td) / ".beads" / "index.json")
            flagged_bead = (idx.get("beads") or {}).get(str(out.get("bead_id"))) or {}
            self.assertTrue(
                flagged_bead.get("structural_coverage_missing"),
                "structural_coverage_missing flag must be written to the bead index",
            )

    def test_mode_observe_overrides_strict_flags(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "observe",
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "0",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "q"}, {"speaker": "assistant", "role": "assistant", "content": "a"}],
                metadata={},
            )
            self.assertTrue(out.get("ok"))
            gate = (out.get("crawler_handoff") or {}).get("agent_authored_gate") or {}
            self.assertEqual("off", gate.get("mode"))
            self.assertTrue(gate.get("used_fallback"))

    def test_mode_enforce_blocks_even_if_legacy_flags_relaxed(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "enforce",
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "0",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "1",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "q"}, {"speaker": "assistant", "role": "assistant", "content": "a"}],
                metadata={},
            )
            self.assertFalse(out.get("ok"))
            self.assertIn(out.get("error_code"), {"agent_callable_missing", "agent_updates_missing"})
            gate = (out.get("crawler_handoff") or {}).get("agent_authored_gate") or {}
            self.assertEqual("hard", gate.get("mode"))

    def test_enforce_invalid_callable_returns_deterministic_gate_error(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "enforce",
                "CORE_MEMORY_AGENT_CRAWLER_CALLABLE": "not_a_module:not_a_fn",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "q"}, {"speaker": "assistant", "role": "assistant", "content": "a"}],
                metadata={},
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual("agent_callable_missing", out.get("error_code"))


if __name__ == "__main__":
    unittest.main()
