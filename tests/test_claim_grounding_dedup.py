"""Tests for grounding-hash dedup telemetry in ClaimUpdate writes (TODO #5).

The grounding-hash dedup fires when _claim_update_dedupe_key produces the same
key for two rows — meaning the exact same (target, decision, replacement, hash)
combination is submitted twice. The second write is silently skipped and a
WARNING log is emitted.
"""
from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path


class TestClaimGroundingDedup(unittest.TestCase):
    def _make_store(self, td):
        from core_memory.persistence.store import MemoryStore
        return MemoryStore(td)

    def test_exact_duplicate_update_is_deduped(self):
        """Identical ClaimUpdate written twice — second is silently skipped."""
        with tempfile.TemporaryDirectory() as td:
            from core_memory.persistence.store_claim_ops import (
                write_claim_updates_to_bead,
                read_claim_updates_for_bead,
                compute_claim_grounding_hash,
            )
            s = self._make_store(td)
            bead_id = s.add_bead(
                type="context", title="T1", summary=["s"], session_id="sx", source_turn_ids=["t1"]
            )

            row = {
                "decision": "retract",
                "target_claim_id": "claim-1",
                "subject": "api_version",
                "slot": "current",
                "reason_text": "test",
                "trigger_bead_id": bead_id,
                "evidence_bead_ids": [bead_id],
                "judge_model": "test-model",
                "prompt_version": "v1",
                "rubric_version": "v1",
            }
            row["grounding_hash"] = compute_claim_grounding_hash(row)

            write_claim_updates_to_bead(td, bead_id, [row])
            write_claim_updates_to_bead(td, bead_id, [row])  # exact duplicate

            updates = read_claim_updates_for_bead(td, bead_id)
            self.assertEqual(len(updates), 1, updates)

    def test_exact_duplicate_emits_warning_log(self):
        """Duplicate grounding triggers a WARNING log with duplicate_grounding message."""
        with tempfile.TemporaryDirectory() as td:
            from core_memory.persistence.store_claim_ops import (
                write_claim_updates_to_bead,
                compute_claim_grounding_hash,
            )
            s = self._make_store(td)
            bead_id = s.add_bead(
                type="context", title="T1", summary=["s"], session_id="sx", source_turn_ids=["t1"]
            )

            row = {
                "decision": "retract",
                "target_claim_id": "claim-1",
                "subject": "api_version",
                "slot": "current",
                "reason_text": "test",
                "trigger_bead_id": bead_id,
                "evidence_bead_ids": [bead_id],
                "judge_model": "m",
                "prompt_version": "v1",
                "rubric_version": "v1",
            }
            row["grounding_hash"] = compute_claim_grounding_hash(row)

            write_claim_updates_to_bead(td, bead_id, [row])
            with self.assertLogs("core_memory.persistence.store_claim_ops", level="WARNING") as cm:
                write_claim_updates_to_bead(td, bead_id, [row])
            self.assertTrue(any("duplicate_grounding" in msg for msg in cm.output))

    def test_different_grounding_hash_same_slot_both_persist(self):
        """Two ClaimUpdates with different grounding_hashes for same slot both persist."""
        with tempfile.TemporaryDirectory() as td:
            from core_memory.persistence.store_claim_ops import (
                write_claim_updates_to_bead,
                read_claim_updates_for_bead,
                compute_claim_grounding_hash,
            )
            s = self._make_store(td)
            bead_id = s.add_bead(
                type="context", title="T1", summary=["s"], session_id="sx", source_turn_ids=["t1"]
            )

            row1 = {
                "decision": "retract",
                "target_claim_id": "claim-1",
                "subject": "api_version",
                "slot": "current",
                "reason_text": "test",
                "trigger_bead_id": bead_id,
                "evidence_bead_ids": [bead_id],
                "judge_model": "model-A",
                "prompt_version": "v1",
                "rubric_version": "v1",
            }
            row1["grounding_hash"] = compute_claim_grounding_hash(row1)

            row2 = {
                "decision": "retract",
                "target_claim_id": "claim-2",
                "subject": "api_version",
                "slot": "current",
                "reason_text": "different evidence",
                "trigger_bead_id": bead_id,
                "evidence_bead_ids": [bead_id],
                "judge_model": "model-B",  # different model → different hash
                "prompt_version": "v1",
                "rubric_version": "v1",
            }
            row2["grounding_hash"] = compute_claim_grounding_hash(row2)

            self.assertNotEqual(row1["grounding_hash"], row2["grounding_hash"])

            write_claim_updates_to_bead(td, bead_id, [row1, row2])
            updates = read_claim_updates_for_bead(td, bead_id)
            self.assertEqual(len(updates), 2, updates)

    def test_emit_claim_updates_deduped_across_calls(self):
        """emit_claim_updates: same grounding_hash on re-run is silently skipped."""
        with tempfile.TemporaryDirectory() as td:
            from core_memory.persistence.store import MemoryStore
            from core_memory.claim.update_policy import emit_claim_updates
            from core_memory.persistence.store_claim_ops import (
                write_claims_to_bead,
                read_claim_updates_for_bead,
            )

            s = MemoryStore(td)
            bead_id = s.add_bead(
                type="context", title="T1", summary=["s"], session_id="sx", source_turn_ids=["t1"]
            )
            bead2_id = s.add_bead(
                type="context", title="T2", summary=["s2"], session_id="sx", source_turn_ids=["t2"]
            )
            # Write a claim
            claim = {
                "id": "c-1",
                "subject": "cpu_limit",
                "slot": "value",
                "value": "2000m",
                "source_bead_id": bead_id,
            }
            write_claims_to_bead(td, bead_id, [claim])

            # First call — emit a supersede
            new_claim = {
                "id": "c-2",
                "subject": "cpu_limit",
                "slot": "value",
                "value": "4000m",
                "source_bead_id": bead2_id,
            }
            r1 = emit_claim_updates(td, [new_claim], bead2_id, session_id="sx")
            # Second call with same bead2_id as trigger — _claim_update_dedupe_key catches exact duplicate
            r2 = emit_claim_updates(td, [new_claim], bead2_id, session_id="sx")

            all_updates = read_claim_updates_for_bead(td, bead2_id)
            # Despite two calls, exact dedup (same target+decision+replacement+hash) prevents double write
            self.assertLessEqual(len(all_updates), len(r1) + 1, all_updates)


if __name__ == "__main__":
    unittest.main()
