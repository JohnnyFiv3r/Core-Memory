import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.session_enrichment_delta import (
    DELTA_QUARANTINE_PATH,
    SCHEMA,
    build_window_context_ref,
    canonical_session_projection,
    crawler_updates_to_delta,
    delta_to_crawler_updates,
    projections_equal,
    write_delta_quarantine,
)


class TestSessionEnrichmentDeltaWindow(unittest.TestCase):
    def test_window_context_ref_captures_explicit_bounds(self):
        ctx = {
            "session_id": "s1",
            "beads": [
                {"id": "b1", "source_turn_ids": ["t1"]},
                {"id": "b2", "source_turn_ids": ["t2"]},
            ],
            "visible_bead_ids": ["b1", "b2", "carry-1"],
            "carry_in_bead_ids": ["carry-1"],
        }

        ref = build_window_context_ref(session_id="s1", crawler_ctx=ctx, window_turn_ids=["t1", "t2"])

        self.assertEqual("s1", ref["session_id"])
        self.assertEqual(2, ref["row_count"])
        self.assertEqual("b1", ref["first_visible_bead_id"])
        self.assertEqual("carry-1", ref["last_visible_bead_id"])
        self.assertEqual("t1", ref["first_visible_turn_id"])
        self.assertEqual("t2", ref["last_visible_turn_id"])
        self.assertIn("carry-1", ref["carry_in_bead_ids"])
        self.assertTrue(str(ref["context_fingerprint"]).startswith("sha256:"))


class TestSessionEnrichmentDeltaAdapter(unittest.TestCase):
    def test_crawler_updates_roundtrip_preserves_current_shape(self):
        updates = {
            "beads_create": [
                {
                    "type": "decision",
                    "title": "Use JSONB",
                    "summary": ["PostgreSQL JSONB keeps writes transactional"],
                    "source_turn_ids": ["t1"],
                    "tags": ["crawler_reviewed"],
                }
            ],
            "promotions": ["b1"],
            "associations": [
                {
                    "source_bead_id": "b2",
                    "target_bead_id": "b1",
                    "relationship": "supports",
                    "reason_text": "The second turn supports the first decision.",
                    "confidence": 0.91,
                    "provenance": "model_inferred",
                    "evidence_fields": ["summary"],
                }
            ],
            "association_lifecycle": [
                {
                    "association_id": "assoc-1",
                    "action": "reaffirm",
                    "reason_text": "Still valid.",
                    "confidence": 0.8,
                }
            ],
        }

        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates=updates,
            crawler_ctx={"session_id": "s1", "visible_bead_ids": ["b1", "b2"]},
            idempotency_key="enrich-s1-t1",
        )
        projected = delta_to_crawler_updates(delta)

        self.assertEqual(SCHEMA, delta["schema"])
        self.assertEqual("enrich-s1-t1", delta["source"]["idempotency_key"])
        self.assertTrue(delta["beads_create"][0]["dedupe_key"].startswith("bead:s1:t1:decision:"))
        self.assertEqual("assoc:b2:b1:supports", delta["associations"][0]["dedupe_key"])
        self.assertEqual(["b1"], projected["promotions"])
        self.assertEqual("supports", projected["associations"][0]["relationship"])
        self.assertEqual("assoc-1", projected["association_lifecycle"][0]["association_id"])

    def test_invalid_association_is_quarantined_not_projected(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"associations": [{"source_bead_id": "b2", "relationship": "supports"}]},
            crawler_ctx={"session_id": "s1", "visible_bead_ids": ["b1", "b2"]},
        )
        projected = delta_to_crawler_updates(delta)
        quarantine = delta["diagnostics"]["quarantine"]

        self.assertEqual([], projected["associations"])
        self.assertEqual(1, delta["diagnostics"]["quarantined"])
        self.assertEqual("associations", quarantine[0]["row_type"])
        self.assertIn("missing_source_target_or_relationship", quarantine[0]["reasons"])

    def test_noncanonical_association_relationship_is_quarantined(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "associations": [
                    {
                        "source_bead_id": "b2",
                        "target_bead_id": "b1",
                        "relationship": "shared_tag",
                        "reason_text": "tag overlap",
                        "confidence": 0.7,
                    }
                ]
            },
            crawler_ctx={"session_id": "s1", "visible_bead_ids": ["b1", "b2"]},
        )
        projected = delta_to_crawler_updates(delta)
        quarantine = delta["diagnostics"]["quarantine"]

        self.assertEqual([], delta["associations"])
        self.assertEqual([], projected["associations"])
        self.assertEqual(1, delta["diagnostics"]["quarantined"])
        self.assertIn("noncanonical_relationship:shared_tag", quarantine[0]["reasons"])

    def test_bead_entities_are_projected_as_delta_entity_upserts(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "beads_create": [
                    {
                        "type": "decision",
                        "title": "OpenAI Platform migration",
                        "summary": ["Open AI Platform was selected."],
                        "source_turn_ids": ["t1"],
                        "entities": ["Open AI Platform", "OpenAI Platform"],
                    }
                ]
            },
            crawler_ctx={"session_id": "s1", "visible_bead_ids": ["b1"]},
        )

        self.assertEqual(1, len(delta["entity_upserts"]))
        entity = delta["entity_upserts"][0]
        self.assertEqual("entity:openaiplatform", entity["dedupe_key"])
        self.assertEqual("openaiplatform", entity["normalized_label"])
        self.assertTrue(str(entity["source_bead_key"]).startswith("bead:s1:t1:decision:"))

    def test_explicit_invalid_entity_upsert_is_quarantined(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"entity_upserts": [{"label": "!!!"}]},
        )

        self.assertEqual([], delta["entity_upserts"])
        self.assertEqual(1, delta["diagnostics"]["quarantined"])
        self.assertIn("invalid_entity_label", delta["diagnostics"]["quarantine"][0]["reasons"])

    def test_entity_upsert_uses_live_registry_noise_policy(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"entity_upserts": [{"label": "the"}]},
        )

        self.assertEqual([], delta["entity_upserts"])
        self.assertEqual(1, delta["diagnostics"]["quarantined"])
        self.assertIn("invalid_entity_label", delta["diagnostics"]["quarantine"][0]["reasons"])

    def test_claims_are_projected_as_delta_rows_with_stable_keys(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "claims": [
                    {
                        "id": "claim-random-1",
                        "claim_kind": "preference",
                        "subject": "user",
                        "slot": "preference_editor",
                        "value": "Neovim",
                        "reason_text": "User said they prefer Neovim.",
                        "confidence": 0.9,
                        "source_bead_id": "b1",
                        "source_turn_ids": ["t1"],
                    },
                    {
                        "id": "claim-random-2",
                        "claim_kind": "preference",
                        "subject": "user",
                        "slot": "preference_editor",
                        "value": "Neovim",
                        "reason_text": "Duplicate semantic claim.",
                        "confidence": 0.7,
                        "source_bead_id": "b1",
                        "source_turn_ids": ["t1"],
                    },
                ]
            },
        )
        projected = delta_to_crawler_updates(delta)

        self.assertEqual(1, len(delta["claims"]))
        self.assertTrue(delta["claims"][0]["dedupe_key"].startswith("claim:user:preference_editor:"))
        self.assertNotIn("claim-random", delta["claims"][0]["dedupe_key"])
        self.assertEqual("claim-random-1", projected["claims"][0]["id"])

    def test_invalid_claim_is_quarantined(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"claims": [{"subject": "user", "slot": "preference", "value": "coffee"}]},
        )

        self.assertEqual([], delta["claims"])
        self.assertEqual(1, delta["diagnostics"]["quarantined"])
        self.assertIn("invalid_claim", delta["diagnostics"]["quarantine"][0]["reasons"])

    def test_claim_missing_live_required_fields_is_quarantined(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "claims": [
                    {
                        "subject": "user",
                        "slot": "preference",
                        "value": "coffee",
                        "reason_text": "User said coffee.",
                    }
                ]
            },
        )

        self.assertEqual([], delta["claims"])
        self.assertEqual(1, delta["diagnostics"]["quarantined"])
        self.assertIn("invalid_claim", delta["diagnostics"]["quarantine"][0]["reasons"])

    def test_claim_updates_are_projected_with_sequence_keys(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "claims": [
                    {
                        "id": "claim-new",
                        "claim_kind": "preference",
                        "subject": "user",
                        "slot": "preference_drink",
                        "value": "tea",
                        "reason_text": "User said they now prefer tea.",
                        "confidence": 0.88,
                        "source_bead_id": "b2",
                    }
                ],
                "claim_updates": [
                    {
                        "decision": "supersede",
                        "target_claim_id": "claim-old",
                        "replacement_claim_id": "claim-new",
                        "subject": "user",
                        "slot": "preference_drink",
                        "trigger_bead_id": "b2",
                        "reason_text": "New preference supersedes old preference.",
                    }
                ],
            },
        )
        projected = delta_to_crawler_updates(delta)

        self.assertEqual(1, len(delta["claim_updates"]))
        update = delta["claim_updates"][0]
        self.assertEqual("claim-update:s1:t1:0", update["sequence_key"])
        self.assertTrue(update["dedupe_key"].startswith("claim-update:"))
        self.assertEqual(delta["claims"][0]["dedupe_key"], update["replacement_claim_key"])
        self.assertEqual("supersede", projected["claim_updates"][0]["decision"])

    def test_invalid_claim_update_is_quarantined(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"claim_updates": [{"decision": "supersede", "target_claim_id": "claim-old"}]},
        )

        self.assertEqual([], delta["claim_updates"])
        self.assertEqual(1, delta["diagnostics"]["quarantined"])
        self.assertIn("invalid_claim_update", delta["diagnostics"]["quarantine"][0]["reasons"])

    def test_duplicate_claim_updates_dedupe_like_live_explicit_updates(self):
        row = {
            "decision": "supersede",
            "target_claim_id": "claim-old",
            "replacement_claim_id": "claim-new",
            "trigger_bead_id": "b2",
            "reason_text": "New claim supersedes old claim.",
        }
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"claim_updates": [dict(row), dict(row)]},
        )

        self.assertEqual(1, len(delta["claim_updates"]))
        self.assertEqual("claim-update:s1:t1:0", delta["claim_updates"][0]["sequence_key"])

    def test_goal_lifecycle_rows_are_projected_into_delta(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "goal_lifecycle": [
                    {
                        "goal_bead_id": "goal-1",
                        "action": "complete",
                        "reason_text": "Outcome bead indicates the migration finished.",
                        "confidence": 0.86,
                    }
                ]
            },
        )
        projected = delta_to_crawler_updates(delta)

        self.assertEqual(1, len(delta["goal_lifecycle"]))
        row = delta["goal_lifecycle"][0]
        self.assertEqual("goal-bead:goal-1", row["goal_key"])
        self.assertEqual("goal-life:s1:t1:0", row["sequence_key"])
        self.assertTrue(row["dedupe_key"].startswith("goal-life:goal-bead:goal-1:complete:"))
        self.assertEqual("complete", projected["goal_lifecycle"][0]["action"])

    def test_invalid_goal_lifecycle_row_is_quarantined(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"goal_lifecycle": [{"goal_bead_id": "goal-1", "action": "resolved"}]},
        )

        self.assertEqual([], delta["goal_lifecycle"])
        self.assertEqual(1, delta["diagnostics"]["quarantined"])
        self.assertIn("invalid_goal_lifecycle", delta["diagnostics"]["quarantine"][0]["reasons"])

    def test_memory_outcome_rows_are_projected_into_delta(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "memory_outcomes": [
                    {
                        "bead_id": "b-turn",
                        "interaction_role": "memory_resolution",
                        "memory_outcome": {
                            "role": "memory_resolution",
                            "description": "Memory directly answered a question.",
                            "bead_count": 2,
                        },
                    }
                ]
            },
        )
        projected = delta_to_crawler_updates(delta)

        self.assertEqual(1, len(delta["memory_outcomes"]))
        row = delta["memory_outcomes"][0]
        self.assertEqual("memory-outcome:b-turn:t1", row["dedupe_key"])
        self.assertEqual("memory_resolution", row["interaction_role"])
        self.assertEqual("memory_resolution", projected["memory_outcomes"][0]["interaction_role"])

    def test_invalid_memory_outcome_row_is_quarantined(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"memory_outcomes": [{"interaction_role": "memory_resolution", "memory_outcome": {}}]},
        )

        self.assertEqual([], delta["memory_outcomes"])
        self.assertEqual(1, delta["diagnostics"]["quarantined"])
        self.assertIn("invalid_memory_outcome", delta["diagnostics"]["quarantine"][0]["reasons"])

    def test_duplicate_memory_outcomes_dedupe_by_bead_and_turn(self):
        row = {
            "bead_id": "b-turn",
            "interaction_role": "memory_reflection",
            "memory_outcome": {"role": "memory_reflection", "description": "Memory was surfaced.", "bead_count": 1},
        }
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"memory_outcomes": [dict(row), dict(row)]},
        )

        self.assertEqual(1, len(delta["memory_outcomes"]))
        self.assertEqual("memory-outcome:b-turn:t1", delta["memory_outcomes"][0]["dedupe_key"])

    def test_array_bounds_quarantine_overflow(self):
        updates = {"promotions": [f"b{i}" for i in range(70)]}
        delta = crawler_updates_to_delta(session_id="s1", turn_id="t1", updates=updates)

        self.assertEqual(64, len(delta["promotions"]))
        self.assertEqual(6, delta["diagnostics"]["quarantined"])
        self.assertEqual("array_bound_exceeded", delta["diagnostics"]["quarantine"][0]["reasons"][0])

    def test_write_delta_quarantine_persists_and_dedupes_rows(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"entity_upserts": [{"label": "!!!"}]},
        )

        with tempfile.TemporaryDirectory() as td:
            first = write_delta_quarantine(td, delta)
            second = write_delta_quarantine(td, delta)
            tempfile_path = Path(td) / DELTA_QUARANTINE_PATH
            rows = [json.loads(line) for line in tempfile_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        self.assertEqual(1, first["written"])
        self.assertEqual(0, first["deduped"])
        self.assertEqual(0, second["written"])
        self.assertEqual(1, second["deduped"])
        self.assertEqual(1, len(rows))
        self.assertEqual(2, rows[0]["seen_count"])
        self.assertEqual("entity_upserts", rows[0]["row_type"])
        self.assertIn("invalid_entity_label", rows[0]["reasons"])
        self.assertEqual(str(tempfile_path), first["path"])

    def test_write_delta_quarantine_serializes_concurrent_replays(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"entity_upserts": [{"label": "!!!"}]},
        )

        with tempfile.TemporaryDirectory() as td:
            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(lambda _i: write_delta_quarantine(td, delta), range(50)))
            tempfile_path = Path(td) / DELTA_QUARANTINE_PATH
            rows = [json.loads(line) for line in tempfile_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        self.assertEqual(1, sum(int(r["written"]) for r in results))
        self.assertEqual(49, sum(int(r["deduped"]) for r in results))
        self.assertEqual(1, len(rows))
        self.assertEqual(50, rows[0]["seen_count"])


class TestCanonicalSessionProjection(unittest.TestCase):
    def test_projection_ignores_volatile_bead_timestamps(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            bead_id = store.add_bead(
                type="decision",
                title="Use JSONB",
                summary=["Keep writes transactional"],
                session_id="s1",
                source_turn_ids=["t1"],
            )
            p1 = canonical_session_projection(td, "s1")

            idx_path = store.beads_dir / "index.json"
            idx = store._read_json(idx_path)
            idx["beads"][bead_id]["updated_at"] = "2099-01-01T00:00:00+00:00"
            idx["beads"][bead_id]["promotion_decided_at"] = "2099-01-01T00:00:00+00:00"
            store._write_json(idx_path, idx)

            p2 = canonical_session_projection(td, "s1")
            self.assertTrue(projections_equal(p1, p2))

    def test_projection_compares_canonical_association_dedupe_content(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            b1 = store.add_bead(type="decision", title="A", summary=["A"], session_id="s1", source_turn_ids=["t1"])
            b2 = store.add_bead(type="context", title="B", summary=["B"], session_id="s1", source_turn_ids=["t2"])
            idx_path = store.beads_dir / "index.json"
            idx = store._read_json(idx_path)
            idx["associations"] = [
                {
                    "id": "assoc-random-1",
                    "source_bead": b2,
                    "target_bead": b1,
                    "relationship": "supports",
                    "created_at": "2020-01-01T00:00:00+00:00",
                }
            ]
            store._write_json(idx_path, idx)
            p1 = canonical_session_projection(td, "s1")

            idx["associations"][0]["id"] = "assoc-random-2"
            idx["associations"][0]["created_at"] = "2099-01-01T00:00:00+00:00"
            store._write_json(idx_path, idx)
            p2 = canonical_session_projection(td, "s1")

            self.assertTrue(projections_equal(p1, p2))
            self.assertIn(":supports", p2["associations"][0]["dedupe_key"])
            self.assertNotIn(b1, p2["associations"][0]["dedupe_key"])
            self.assertNotIn(b2, p2["associations"][0]["dedupe_key"])

    def test_projection_ignores_generated_bead_and_claim_ids(self):
        def build(root: str, claim_id: str, update_id: str) -> None:
            store = MemoryStore(root)
            b1 = store.add_bead(
                type="decision",
                title="Use JSONB",
                summary=["Keep writes transactional"],
                session_id="s1",
                source_turn_ids=["t1"],
            )
            b2 = store.add_bead(
                type="context",
                title="JSONB rationale",
                summary=["The rationale supports the decision"],
                session_id="s1",
                source_turn_ids=["t2"],
            )
            idx_path = store.beads_dir / "index.json"
            idx = store._read_json(idx_path)
            idx["beads"][b1]["claims"] = [
                {
                    "id": claim_id,
                    "subject": "PostgreSQL",
                    "slot": "storage_choice",
                    "value": "JSONB",
                    "source_bead_id": b1,
                    "source_turn_ids": ["t1"],
                }
            ]
            idx["beads"][b2]["claim_updates"] = [
                {
                    "id": update_id,
                    "decision": "reaffirm",
                    "target_claim_id": claim_id,
                    "subject": "PostgreSQL",
                    "slot": "storage_choice",
                    "trigger_bead_id": b2,
                    "reason_text": "Still supported by the session window.",
                }
            ]
            idx["associations"] = [
                {
                    "id": f"assoc-{claim_id}",
                    "source_bead": b2,
                    "target_bead": b1,
                    "relationship": "supports",
                    "reaffirmed_at": "2020-01-01T00:00:00+00:00",
                }
            ]
            store._write_json(idx_path, idx)

        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            build(left, "claim-left", "update-left")
            build(right, "claim-right", "update-right")

            p1 = canonical_session_projection(left, "s1")
            p2 = canonical_session_projection(right, "s1")

            self.assertTrue(projections_equal(p1, p2))
            self.assertNotIn("claim-left", str(p1))
            self.assertNotIn("update-left", str(p1))
            self.assertNotIn("reaffirmed_at", str(p1))


if __name__ == "__main__":
    unittest.main()
