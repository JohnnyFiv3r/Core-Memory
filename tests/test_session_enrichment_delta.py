import json
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.session.session_enrichment_delta import (
    DELTA_QUARANTINE_PATH,
    DELTA_ROW_LIMITS,
    DELTA_ROW_TYPES,
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

    def test_association_target_outside_visible_window_is_quarantined(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"associations": [{"source_bead_id": "b2", "target_bead_id": "b9", "relationship": "supports"}]},
            crawler_ctx={"session_id": "s1", "visible_bead_ids": ["b1", "b2"]},
        )
        projected = delta_to_crawler_updates(delta)

        self.assertEqual([], delta["associations"])
        self.assertEqual([], projected["associations"])
        self.assertEqual(1, delta["diagnostics"]["quarantined"])
        self.assertIn("target_outside_visible_window", delta["diagnostics"]["quarantine"][0]["reasons"])

    def test_association_source_outside_visible_window_is_quarantined(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"associations": [{"source_bead_id": "b9", "target_bead_id": "b1", "relationship": "supports"}]},
            crawler_ctx={"session_id": "s1", "visible_bead_ids": ["b1", "b2"]},
        )
        projected = delta_to_crawler_updates(delta)

        self.assertEqual([], delta["associations"])
        self.assertEqual([], projected["associations"])
        self.assertEqual(1, delta["diagnostics"]["quarantined"])
        self.assertIn("source_outside_visible_window", delta["diagnostics"]["quarantine"][0]["reasons"])

    def test_association_target_with_empty_visible_window_is_quarantined(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"associations": [{"source_bead_id": "b2", "target_bead_id": "b1", "relationship": "supports"}]},
        )
        projected = delta_to_crawler_updates(delta)

        self.assertEqual([], delta["window_context_ref"]["visible_bead_ids"])
        self.assertEqual([], delta["associations"])
        self.assertEqual([], projected["associations"])
        self.assertEqual(1, delta["diagnostics"]["quarantined"])
        self.assertIn("target_outside_visible_window", delta["diagnostics"]["quarantine"][0]["reasons"])

    def test_historical_session_association_scope_allows_nonvisible_targets(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "association_scope": "historical_session",
                "associations": [{"source_bead_id": "b2", "target_bead_id": "b9", "relationship": "supports"}],
            },
            crawler_ctx={"session_id": "s1", "visible_bead_ids": ["b1", "b2"]},
        )
        projected = delta_to_crawler_updates(delta)

        self.assertEqual("historical_session", delta["association_scope"])
        self.assertEqual(1, len(delta["associations"]))
        self.assertEqual("b9", delta["associations"][0]["target_bead_id"])
        self.assertEqual("historical_session", projected["association_scope"])
        self.assertEqual("b9", projected["associations"][0]["target_bead_id"])

    def test_historical_session_association_scope_allows_empty_visible_window(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "association_scope": "historical_session",
                "associations": [{"source_bead_id": "b2", "target_bead_id": "b1", "relationship": "supports"}],
            },
        )

        self.assertEqual([], delta["window_context_ref"]["visible_bead_ids"])
        self.assertEqual(1, len(delta["associations"]))
        self.assertEqual(0, delta["diagnostics"]["quarantined"])


    def test_future_delta_rows_are_reserved_diagnostic_only(self):
        updates = {
            "entity_upserts": [{"label": "OpenAI Platform"}],
            "claims": [{"subject": "user", "slot": "preference", "value": "coffee"}],
            "claim_updates": [{"decision": "supersede", "target_claim_id": "c1"}],
            "goal_lifecycle": [{"goal_bead_id": "g1", "action": "complete"}],
            "memory_outcomes": [{"bead_id": "b1", "interaction_role": "memory_resolution"}],
        }
        delta = crawler_updates_to_delta(session_id="s1", turn_id="t1", updates=updates)
        projected = delta_to_crawler_updates(delta)

        for row_type in ("entity_upserts", "claims", "claim_updates", "goal_lifecycle", "memory_outcomes"):
            self.assertEqual(0, DELTA_ROW_LIMITS[row_type])
            self.assertEqual([], delta[row_type])
            self.assertEqual(1, delta["diagnostics"]["reserved_input_counts"][row_type])
            self.assertEqual([], projected.get(row_type, []))
        self.assertEqual(0, delta["diagnostics"]["quarantined"])

    def test_array_bounds_quarantine_overflow(self):
        updates = {"promotions": [f"b{i}" for i in range(70)]}
        delta = crawler_updates_to_delta(session_id="s1", turn_id="t1", updates=updates)

        self.assertEqual(64, len(delta["promotions"]))
        self.assertEqual(6, delta["diagnostics"]["quarantined"])
        self.assertEqual("array_bound_exceeded", delta["diagnostics"]["quarantine"][0]["reasons"][0])

    def _valid_row_for_type(self, row_type, i=0):
        row_for = {
            "beads_create": lambda n: {"type": "context", "title": f"B{n}", "summary": [f"S{n}"], "source_turn_ids": [f"t{n}"]},
            "promotions": lambda n: f"b{n}",
            "associations": lambda n: {"source_bead_id": f"b{n}-src", "target_bead_id": f"b{n}-tgt", "relationship": "supports"},
            "association_lifecycle": lambda n: {"association_id": f"assoc-{n}", "action": "reaffirm"},
            "entity_upserts": lambda n: {"label": f"Entity {n}"},
            "claims": lambda n: {
                "id": f"claim-{n}",
                "claim_kind": "custom",
                "subject": f"subject-{n}",
                "slot": "status",
                "value": f"value-{n}",
                "reason_text": "explicit bounded-row test",
                "confidence": 0.8,
                "source_turn_ids": ["t1"],
            },
            "claim_updates": lambda n: {"decision": "reaffirm", "target_claim_id": f"claim-{n}", "reason_text": "still valid"},
            "goal_lifecycle": lambda n: {"title": f"Ship phase {n}", "action": "progress"},
            "memory_outcomes": lambda n: {
                "bead_id": f"b{n}",
                "interaction_role": "memory_reflection",
                "memory_outcome": {"role": "memory_reflection", "description": f"Outcome {n}", "bead_count": 1},
            },
        }
        return row_for[row_type](i)

    def test_active_array_bounds_are_explicit_and_quarantine_overflow(self):
        active_row_types = ("beads_create", "promotions", "associations", "association_lifecycle")
        for row_type in active_row_types:
            limit = DELTA_ROW_LIMITS[row_type]
            with self.subTest(row_type=row_type):
                crawler_ctx = None
                if row_type == "associations":
                    crawler_ctx = {
                        "session_id": "s1",
                        "visible_bead_ids": [x for i in range(limit + 1) for x in (f"b{i}-src", f"b{i}-tgt")],
                    }
                delta = crawler_updates_to_delta(
                    session_id="s1",
                    turn_id="t1",
                    updates={row_type: [self._valid_row_for_type(row_type, i) for i in range(limit + 1)]},
                    crawler_ctx=crawler_ctx,
                )

                self.assertEqual(limit, len(delta[row_type]))
                self.assertEqual(DELTA_ROW_LIMITS, delta["diagnostics"]["row_limits"])
                self.assertEqual(1, delta["diagnostics"]["quarantined"])
                overflow = delta["diagnostics"]["quarantine"][0]
                self.assertEqual(row_type, overflow["row_type"])
                self.assertEqual(["array_bound_exceeded"], overflow["reasons"])

    def test_diagnostics_report_counts_for_all_row_types(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "promotions": ["b1"],
                "entity_upserts": [{"label": "!!!"}],
            },
        )

        self.assertEqual(list(DELTA_ROW_TYPES), list(delta["diagnostics"]["accepted_counts"].keys()))
        self.assertEqual(list(DELTA_ROW_TYPES), list(delta["diagnostics"]["quarantined_counts"].keys()))
        self.assertEqual(1, delta["diagnostics"]["accepted_counts"]["promotions"])
        self.assertEqual(1, delta["diagnostics"]["reserved_input_counts"]["entity_upserts"])
        self.assertEqual(0, delta["diagnostics"]["quarantined_counts"]["entity_upserts"])

    def test_active_accepted_row_types_emit_common_contract_fields(self):
        for row_type in ("beads_create", "promotions", "associations", "association_lifecycle"):
            with self.subTest(row_type=row_type):
                crawler_ctx = None
                if row_type == "associations":
                    crawler_ctx = {"session_id": "s1", "visible_bead_ids": ["b0-src", "b0-tgt"]}
                delta = crawler_updates_to_delta(
                    session_id="s1",
                    turn_id="t1",
                    updates={row_type: [self._valid_row_for_type(row_type)]},
                    crawler_ctx=crawler_ctx,
                )

                self.assertEqual(1, delta["diagnostics"]["accepted_counts"][row_type])
                self.assertEqual(0, delta["diagnostics"]["quarantined_counts"][row_type])
                row = delta[row_type][0]
                self.assertTrue(row.get("dedupe_key"))
                self.assertIsInstance(row.get("confidence"), float)
                self.assertGreaterEqual(row["confidence"], 0.0)
                self.assertLessEqual(row["confidence"], 1.0)
                self.assertTrue(row.get("context_fingerprint", "").startswith("sha256:"))
                self.assertIsInstance(row.get("provenance"), dict)
                self.assertTrue(row["provenance"].get("kind"))
                self.assertIsInstance(row.get("evidence_refs"), list)

    def test_common_rows_get_context_fingerprint_grounding_ref_by_default(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"promotions": ["b1"]},
            crawler_ctx={"session_id": "s1", "visible_bead_ids": ["b1"]},
        )

        row = delta["promotions"][0]
        self.assertEqual(delta["window_context_ref"]["context_fingerprint"], row["context_fingerprint"])
        self.assertEqual(
            [
                {
                    "kind": "context_fingerprint",
                    "id": row["context_fingerprint"],
                    "field": None,
                    "quote": None,
                    "hash": row["context_fingerprint"],
                }
            ],
            row["evidence_refs"],
        )

    def test_fallback_rows_can_omit_grounding_refs(self):
        fallback_delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "associations": [
                    {
                        "source_bead_id": "b2",
                        "target_bead_id": "b1",
                        "relationship": "supports",
                        "provenance": "fallback",
                    }
                ]
            },
            crawler_ctx={"session_id": "s1", "visible_bead_ids": ["b1", "b2"]},
        )

        self.assertEqual([], fallback_delta["associations"][0]["evidence_refs"])

    def test_top_level_fallback_source_can_omit_grounding_refs(self):
        fallback_delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"promotions": ["b1"]},
            source_kind="fallback",
        )

        self.assertEqual("fallback", fallback_delta["source"]["kind"])
        self.assertEqual("fallback", fallback_delta["promotions"][0]["provenance"]["kind"])
        self.assertEqual([], fallback_delta["promotions"][0]["evidence_refs"])

    def test_top_level_test_source_can_omit_grounding_refs(self):
        test_delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"promotions": ["b1"]},
            source_kind="test",
        )

        self.assertEqual("test", test_delta["source"]["kind"])
        self.assertEqual("test", test_delta["promotions"][0]["provenance"]["kind"])
        self.assertEqual([], test_delta["promotions"][0]["evidence_refs"])

    def test_explicit_grounding_refs_survive_projection(self):
        ref = {"kind": "turn", "id": "t1", "field": "user_query", "quote": "hello", "hash": None}
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "beads_create": [
                    {
                        "type": "decision",
                        "title": "Use JSONB",
                        "summary": ["PostgreSQL JSONB keeps writes transactional"],
                        "source_turn_ids": ["t1"],
                        "evidence_refs": [ref],
                    }
                ]
            },
        )
        projected = delta_to_crawler_updates(delta)

        self.assertEqual([ref], delta["beads_create"][0]["evidence_refs"])
        self.assertEqual([ref], projected["beads_create"][0]["evidence_refs"])

    def test_association_explicit_evidence_refs_survive_projection(self):
        ref = {"kind": "turn", "id": "t1", "field": "assistant_final", "quote": "because", "hash": None}
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "associations": [
                    {
                        "source_bead_id": "b2",
                        "target_bead_id": "b1",
                        "relationship": "supports",
                        "evidence_refs": [ref],
                    }
                ]
            },
            crawler_ctx={"session_id": "s1", "visible_bead_ids": ["b1", "b2"]},
        )
        projected = delta_to_crawler_updates(delta)

        self.assertEqual([ref], delta["associations"][0]["evidence_refs"])
        self.assertEqual([ref], projected["associations"][0]["evidence_refs"])

    def test_association_default_grounding_refs_do_not_leak_back_to_crawler_shape(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={
                "associations": [
                    {"source_bead_id": "b2", "target_bead_id": "b1", "relationship": "supports"}
                ]
            },
            crawler_ctx={"session_id": "s1", "visible_bead_ids": ["b1", "b2"]},
        )
        projected = delta_to_crawler_updates(delta)

        self.assertEqual("context_fingerprint", delta["associations"][0]["evidence_refs"][0]["kind"])
        self.assertNotIn("evidence_refs", projected["associations"][0])

    def test_write_delta_quarantine_persists_and_dedupes_rows(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"associations": [{"source_bead_id": "b2", "relationship": "supports"}]},
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
        self.assertEqual("associations", rows[0]["row_type"])
        self.assertIn("missing_source_target_or_relationship", rows[0]["reasons"])
        self.assertEqual(str(tempfile_path), first["path"])

    def test_write_delta_quarantine_serializes_concurrent_replays(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"associations": [{"source_bead_id": "b2", "relationship": "supports"}]},
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
    def _seed_two_bead_store(self, root: str) -> tuple[str, str]:
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
        return str(b1), str(b2)

    def test_delta_projection_matches_direct_crawler_committed_state(self):
        from core_memory.association.crawler_contract import apply_crawler_updates, merge_crawler_updates

        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as td:
            store = MemoryStore(base)
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
            left = str(Path(td) / "direct")
            right = str(Path(td) / "delta")
            shutil.copytree(base, left)
            shutil.copytree(base, right)

            updates = {
                "beads_create": [
                    {
                        "type": "insight",
                        "title": "JSONB write durability",
                        "summary": ["Atomic JSONB updates preserve the committed decision."],
                        "source_turn_ids": ["t3"],
                        "tags": ["storage"],
                    }
                ],
                "promotions": [b1],
                "associations": [
                    {
                        "source_bead_id": b2,
                        "target_bead_id": b1,
                        "relationship": "supports",
                        "reason_text": "The rationale supports the original storage decision.",
                        "confidence": 0.9,
                    }
                ],
            }

            apply_crawler_updates(left, "s1", updates, visible_bead_ids=[b1, b2])
            merge_crawler_updates(root=left, session_id="s1")

            delta = crawler_updates_to_delta(
                session_id="s1",
                turn_id="t3",
                updates=updates,
                crawler_ctx={"session_id": "s1", "visible_bead_ids": [b1, b2]},
            )
            apply_crawler_updates(right, "s1", delta_to_crawler_updates(delta), visible_bead_ids=[b1, b2])
            merge_crawler_updates(root=right, session_id="s1")

            self.assertTrue(
                projections_equal(canonical_session_projection(left, "s1"), canonical_session_projection(right, "s1"))
            )

    def test_delta_projection_matches_direct_entity_registry_projection(self):
        from core_memory.association.crawler_contract import apply_crawler_updates, merge_crawler_updates

        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as td:
            self._seed_two_bead_store(base)
            left = str(Path(td) / "direct")
            right = str(Path(td) / "delta")
            shutil.copytree(base, left)
            shutil.copytree(base, right)

            updates = {
                "beads_create": [
                    {
                        "type": "decision",
                        "title": "OpenAI Platform migration",
                        "summary": ["OpenAI Platform was selected for the migration."],
                        "source_turn_ids": ["t3"],
                        "entities": ["OpenAI Platform", "Open AI Platform"],
                    }
                ]
            }

            apply_crawler_updates(left, "s1", updates)
            merge_crawler_updates(root=left, session_id="s1")

            delta = crawler_updates_to_delta(session_id="s1", turn_id="t3", updates=updates)
            apply_crawler_updates(right, "s1", delta_to_crawler_updates(delta))
            merge_crawler_updates(root=right, session_id="s1")

            left_projection = canonical_session_projection(left, "s1")
            right_projection = canonical_session_projection(right, "s1")
            self.assertTrue(projections_equal(left_projection, right_projection))
            self.assertIn("entities", left_projection)

    def test_delta_projection_matches_direct_promotion_and_association_lifecycle_state(self):
        from core_memory.association.crawler_contract import apply_crawler_updates, merge_crawler_updates

        def seed_assoc(root: str, source: str, target: str) -> None:
            idx_path = Path(root) / ".beads" / "index.json"
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            idx["associations"] = [
                {
                    "id": "assoc-jsonb",
                    "type": "association",
                    "source_bead": source,
                    "target_bead": target,
                    "relationship": "supports",
                    "status": "active",
                    "confidence": 0.5,
                    "created_at": "2020-01-01T00:00:00+00:00",
                }
            ]
            idx_path.write_text(json.dumps(idx, indent=2), encoding="utf-8")

        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as td:
            b1, b2 = self._seed_two_bead_store(base)
            seed_assoc(base, b2, b1)
            left = str(Path(td) / "direct")
            right = str(Path(td) / "delta")
            shutil.copytree(base, left)
            shutil.copytree(base, right)

            updates = {
                "promotions": [b1],
                "association_lifecycle": [
                    {
                        "association_id": "assoc-jsonb",
                        "action": "reaffirm",
                        "reason_text": "Still supported.",
                        "confidence": 0.8,
                    }
                ],
            }

            apply_crawler_updates(left, "s1", updates, visible_bead_ids=[b1, b2])
            merge_crawler_updates(root=left, session_id="s1")

            projected = delta_to_crawler_updates(
                crawler_updates_to_delta(
                    session_id="s1",
                    turn_id="t3",
                    updates=updates,
                    crawler_ctx={"session_id": "s1", "visible_bead_ids": [b1, b2]},
                )
            )
            apply_crawler_updates(right, "s1", projected, visible_bead_ids=[b1, b2])
            merge_crawler_updates(root=right, session_id="s1")

            self.assertTrue(
                projections_equal(canonical_session_projection(left, "s1"), canonical_session_projection(right, "s1"))
            )

    def test_delta_projected_promotion_and_lifecycle_are_rerun_idempotent(self):
        from core_memory.association.crawler_contract import apply_crawler_updates, merge_crawler_updates

        with tempfile.TemporaryDirectory() as td:
            b1, b2 = self._seed_two_bead_store(td)
            idx_path = Path(td) / ".beads" / "index.json"
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            idx["associations"] = [
                {
                    "id": "assoc-jsonb",
                    "type": "association",
                    "source_bead": b2,
                    "target_bead": b1,
                    "relationship": "supports",
                    "status": "active",
                    "confidence": 0.5,
                    "created_at": "2020-01-01T00:00:00+00:00",
                }
            ]
            idx_path.write_text(json.dumps(idx, indent=2), encoding="utf-8")
            updates = {
                "promotions": [b1],
                "association_lifecycle": [
                    {
                        "association_id": "assoc-jsonb",
                        "action": "reaffirm",
                        "reason_text": "Still supported.",
                        "confidence": 0.8,
                    }
                ],
            }
            projected = delta_to_crawler_updates(
                crawler_updates_to_delta(
                    session_id="s1",
                    turn_id="t3",
                    updates=updates,
                    crawler_ctx={"session_id": "s1", "visible_bead_ids": [b1, b2]},
                )
            )

            apply_crawler_updates(td, "s1", projected, visible_bead_ids=[b1, b2])
            merge_crawler_updates(root=td, session_id="s1")
            first = canonical_session_projection(td, "s1")

            apply_crawler_updates(td, "s1", projected, visible_bead_ids=[b1, b2])
            merge_crawler_updates(root=td, session_id="s1")
            second = canonical_session_projection(td, "s1")

            self.assertTrue(projections_equal(first, second))
            self.assertEqual(1, len(second["associations"]))
            self.assertEqual("active", second["associations"][0]["status"])
            self.assertEqual(0.8, second["associations"][0]["confidence"])

    def test_delta_projection_replay_is_idempotent_for_side_effect_rows(self):
        from core_memory.association.crawler_contract import apply_crawler_updates, merge_crawler_updates

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
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
            updates = {
                "promotions": [b1],
                "associations": [
                    {
                        "source_bead_id": b2,
                        "target_bead_id": b1,
                        "relationship": "supports",
                        "reason_text": "The rationale supports the original storage decision.",
                        "confidence": 0.9,
                    }
                ],
            }
            projected = delta_to_crawler_updates(
                crawler_updates_to_delta(
                    session_id="s1",
                    turn_id="t3",
                    updates=updates,
                    crawler_ctx={"session_id": "s1", "visible_bead_ids": [b1, b2]},
                    idempotency_key="enrich-s1-t3",
                )
            )

            apply_crawler_updates(td, "s1", projected, visible_bead_ids=[b1, b2])
            merge_crawler_updates(root=td, session_id="s1")
            first = canonical_session_projection(td, "s1")

            apply_crawler_updates(td, "s1", projected, visible_bead_ids=[b1, b2])
            merge_crawler_updates(root=td, session_id="s1")
            second = canonical_session_projection(td, "s1")

            self.assertTrue(projections_equal(first, second))
            self.assertEqual(1, len(second["associations"]))
            self.assertTrue(second["beads"][second["visible_bead_keys"][0]]["promotion_marked"])

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
