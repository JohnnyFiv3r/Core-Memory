import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.store_claim_ops import (
    find_canonical_turn_bead_id,
    read_claims_for_bead,
    read_claim_updates_for_bead,
    resolve_current_state,
    write_memory_outcome_to_bead,
    write_claim_updates_to_bead,
    write_claims_to_bead,
)


def make_claim(claim_id, subject="user", slot="preference", value="coffee"):
    return {
        "id": claim_id,
        "claim_kind": "preference",
        "subject": subject,
        "slot": slot,
        "value": value,
        "reason_text": "stated in turn",
        "confidence": 0.8,
    }


class TestStoreClaimOps(unittest.TestCase):
    def test_find_canonical_turn_bead_id_prefers_seeded_finalized(self):
        with tempfile.TemporaryDirectory() as td:
            # seed index with multiple turn candidates
            from pathlib import Path
            import json

            p = Path(td) / ".beads" / "index.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps(
                    {
                        "beads": {
                            "b1": {
                                "id": "b1",
                                "session_id": "s1",
                                "source_turn_ids": ["t1"],
                                "tags": ["turn_finalized"],
                                "created_at": "2026-01-01T00:00:01Z",
                            },
                            "b2": {
                                "id": "b2",
                                "session_id": "s1",
                                "source_turn_ids": ["t1"],
                                "tags": ["turn_finalized", "seeded_by_engine"],
                                "created_at": "2026-01-01T00:00:00Z",
                            },
                        },
                        "associations": [],
                    }
                ),
                encoding="utf-8",
            )
            bid = find_canonical_turn_bead_id(td, session_id="s1", turn_id="t1", preferred_bead_ids=["b1", "b2"])
            self.assertEqual("b2", bid)

    def test_canonical_storage_is_bead_embedded_index(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(td, "bead1", [make_claim("c1")])
            index_path = Path(td) / ".beads" / "index.json"
            legacy_sidecar = Path(td) / "bead1" / "claims.json"
            self.assertTrue(index_path.exists())
            self.assertFalse(legacy_sidecar.exists())

    def test_write_and_read_claims(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(td, "bead1", [make_claim("c1")])
            result = read_claims_for_bead(td, "bead1")
        self.assertEqual(1, len(result))
        self.assertEqual("c1", result[0]["id"])

    def test_append_claims(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(td, "bead1", [make_claim("c1")])
            write_claims_to_bead(td, "bead1", [make_claim("c2")])
            result = read_claims_for_bead(td, "bead1")
        self.assertEqual(2, len(result))

    def test_read_empty_bead(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual([], read_claims_for_bead(td, "nonexistent"))

    def test_resolve_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            result = resolve_current_state(td, "user", "preference")
        self.assertEqual("not_found", result["status"])
        self.assertIsNone(result["current_claim"])

    def test_resolve_active_claim(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(td, "bead1", [make_claim("c1", subject="user", slot="preference", value="coffee")])
            result = resolve_current_state(td, "user", "preference")
        self.assertEqual("active", result["status"])
        self.assertEqual("c1", result["current_claim"]["id"])

    def test_resolve_retraction(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(td, "bead1", [make_claim("c1")])
            update = {
                "id": "u1",
                "decision": "retract",
                "target_claim_id": "c1",
                "subject": "user",
                "slot": "preference",
                "reason_text": "no longer true",
                "trigger_bead_id": "bead2",
            }
            write_claim_updates_to_bead(td, "bead2", [update])
            result = resolve_current_state(td, "user", "preference")
        self.assertIsNone(result["current_claim"])
        self.assertEqual("retracted", result["status"])

    def test_resolve_supersession(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(td, "bead1", [make_claim("c1", value="coffee")])
            write_claims_to_bead(td, "bead2", [make_claim("c2", value="tea")])
            update = {
                "id": "u1",
                "decision": "supersede",
                "target_claim_id": "c1",
                "subject": "user",
                "slot": "preference",
                "reason_text": "changed preference",
                "trigger_bead_id": "bead2",
            }
            write_claim_updates_to_bead(td, "bead2", [update])
            result = resolve_current_state(td, "user", "preference")
        self.assertEqual("c2", result["current_claim"]["id"])

    def test_write_claim_updates(self):
        with tempfile.TemporaryDirectory() as td:
            update = {
                "id": "u1",
                "decision": "reaffirm",
                "target_claim_id": "c1",
                "subject": "user",
                "slot": "preference",
                "reason_text": "still true",
                "trigger_bead_id": "bead1",
            }
            write_claim_updates_to_bead(td, "bead1", [update])
            result = read_claim_updates_for_bead(td, "bead1")
        self.assertEqual(1, len(result))
        self.assertEqual("reaffirm", result[0]["decision"])

    def test_write_memory_outcome_to_bead(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(td, "bead1", [make_claim("c1")])
            write_memory_outcome_to_bead(
                td,
                "bead1",
                interaction_role="memory_resolution",
                memory_outcome={"role": "memory_resolution", "description": "x", "bead_count": 1},
            )

            from pathlib import Path
            import json

            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            row = (idx.get("beads") or {}).get("bead1") or {}
            self.assertEqual("memory_resolution", row.get("interaction_role"))
            self.assertIsInstance(row.get("memory_outcome"), dict)


if __name__ == "__main__":
    unittest.main()
