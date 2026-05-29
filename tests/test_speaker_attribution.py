"""Tests for multi-speaker attribution and identity persistence (#10).

Three PRD-specified fixtures:
  (a) Same speaker label in two sessions resolves to the same entity_id.
  (b) Two different labels that normalize identically merge to the same entity.
  (c) Low-confidence label is stored as unresolved without creating a spurious entity.

Additional coverage:
  - register_speaker_alias() adds an alias to an existing entity
  - SpeakerAttribution dataclass exists in schema.models
  - _resolve_envelope_speakers() attaches speaker_attribution to envelope metadata
  - store_add_bead_ops promotes attributed_entity_id from speaker_attribution
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.entity.registry import load_entity_registry
from core_memory.entity.speaker_resolver import SpeakerResolution, resolve_speaker


def _empty_index() -> dict:
    return {"entities": {}, "entity_aliases": {}}


class TestResolveFixtureA(unittest.TestCase):
    """(a) Same speaker label in two sessions resolves to the same entity_id."""

    def test_same_label_same_entity_id(self):
        index = _empty_index()
        res1 = resolve_speaker(index, "johnnyfiv3r", "discord")
        res2 = resolve_speaker(index, "johnnyfiv3r", "discord")
        self.assertIsNotNone(res1.resolved_entity_id)
        self.assertEqual(res1.resolved_entity_id, res2.resolved_entity_id)

    def test_same_label_across_source_systems(self):
        index = _empty_index()
        res1 = resolve_speaker(index, "johnnyfiv3r", "discord")
        res2 = resolve_speaker(index, "johnnyfiv3r", "github")
        self.assertEqual(res1.resolved_entity_id, res2.resolved_entity_id)

    def test_resolved_flag_true_for_valid_label(self):
        index = _empty_index()
        res = resolve_speaker(index, "johnnyfiv3r", "discord")
        self.assertTrue(res.resolved)
        self.assertGreaterEqual(res.resolution_confidence, 0.75)


class TestResolveFixtureB(unittest.TestCase):
    """(b) Two different labels that normalize identically merge to the same entity."""

    def test_at_prefix_stripped_and_merges(self):
        index = _empty_index()
        res1 = resolve_speaker(index, "johnnyfiv3r", "discord")
        res2 = resolve_speaker(index, "@johnnyfiv3r", "slack")
        self.assertEqual(res1.resolved_entity_id, res2.resolved_entity_id)

    def test_discord_discriminator_stripped_and_merges(self):
        index = _empty_index()
        res1 = resolve_speaker(index, "johnnyfiv3r", "discord")
        res2 = resolve_speaker(index, "johnnyfiv3r#1234", "discord")
        self.assertEqual(res1.resolved_entity_id, res2.resolved_entity_id)

    def test_second_resolution_is_exact_match(self):
        """After first resolution creates an entity, second should be exact match (1.0)."""
        index = _empty_index()
        resolve_speaker(index, "johnnyfiv3r", "discord")
        res2 = resolve_speaker(index, "johnnyfiv3r", "github")
        self.assertEqual(res2.resolution_confidence, 1.0)

    def test_slack_id_normalizes_correctly(self):
        index = _empty_index()
        res1 = resolve_speaker(index, "@U12345ABCDE", "slack")
        res2 = resolve_speaker(index, "U12345ABCDE", "slack")
        self.assertIsNotNone(res1.resolved_entity_id)
        self.assertEqual(res1.resolved_entity_id, res2.resolved_entity_id)

    def test_zoom_speaker_label_valid(self):
        """SPEAKER_00 has a digit so passes _is_valid_entity_alias."""
        index = _empty_index()
        res = resolve_speaker(index, "SPEAKER_00", "zoom")
        self.assertIsNotNone(res.resolved_entity_id)
        self.assertTrue(res.resolved)


class TestResolveFixtureC(unittest.TestCase):
    """(c) Low-confidence label stored as unresolved without creating a spurious entity."""

    def test_single_char_label_unresolved(self):
        index = _empty_index()
        res = resolve_speaker(index, "U", "slack")
        self.assertIsNone(res.resolved_entity_id)
        self.assertFalse(res.resolved)
        self.assertEqual(res.resolution_confidence, 0.0)
        self.assertEqual(len(index.get("entities", {})), 0)

    def test_empty_label_unresolved(self):
        index = _empty_index()
        res = resolve_speaker(index, "", "slack")
        self.assertIsNone(res.resolved_entity_id)
        self.assertFalse(res.resolved)
        self.assertEqual(len(index.get("entities", {})), 0)

    def test_stopword_label_unresolved(self):
        index = _empty_index()
        res = resolve_speaker(index, "the", "slack")
        self.assertIsNone(res.resolved_entity_id)
        self.assertFalse(res.resolved)
        self.assertEqual(len(index.get("entities", {})), 0)

    def test_very_short_label_no_digits_unresolved(self):
        index = _empty_index()
        res = resolve_speaker(index, "Jo", "slack")
        self.assertIsNone(res.resolved_entity_id)
        self.assertFalse(res.resolved)
        self.assertEqual(len(index.get("entities", {})), 0)

    def test_unresolved_confidence_below_threshold(self):
        index = _empty_index()
        res = resolve_speaker(index, "X", "discord")
        self.assertLess(res.resolution_confidence, 0.75)


class TestRegisterSpeakerAlias(unittest.TestCase):
    def test_register_adds_alias_to_existing_entity(self):
        from core_memory.entity.registry import register_speaker_alias

        index = _empty_index()
        res = resolve_speaker(index, "johnnyfiv3r", "discord")
        eid = res.resolved_entity_id

        result = register_speaker_alias(index, eid, "@johnnyfiv3r", "slack")
        self.assertTrue(result.get("ok"))

        entity = (index.get("entities") or {}).get(eid)
        aliases = list(entity.get("aliases") or [])
        self.assertTrue(any("johnnyfiv3r" in a for a in aliases))

    def test_register_alias_unknown_entity_id_fails(self):
        from core_memory.entity.registry import register_speaker_alias

        index = _empty_index()
        result = register_speaker_alias(index, "entity-nonexistent", "@johnnyfiv3r", "slack")
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "entity_not_found")

    def test_register_alias_empty_args_fails(self):
        from core_memory.entity.registry import register_speaker_alias

        index = _empty_index()
        result = register_speaker_alias(index, "", "@johnnyfiv3r", "slack")
        self.assertFalse(result.get("ok"))


class TestSpeakerAttributionDataclass(unittest.TestCase):
    def test_speaker_attribution_in_schema_models(self):
        from core_memory.schema.models import SpeakerAttribution

        attr = SpeakerAttribution(
            speaker_observed="johnnyfiv3r",
            resolved_entity_id="entity-abc123",
            resolution_confidence=0.9,
            source_system="discord",
            aliases=["johnnyfiv3r", "@johnnyfiv3r"],
            resolved=True,
        )
        self.assertEqual(attr.speaker_observed, "johnnyfiv3r")
        self.assertEqual(attr.resolution_confidence, 0.9)
        self.assertTrue(attr.resolved)


class TestEnvelopeSpeakerResolution(unittest.TestCase):
    def test_resolve_envelope_speakers_attaches_attribution(self):
        from core_memory.transcript_ingest import _resolve_envelope_speakers

        envelopes = [
            {
                "session_id": "sess-1",
                "turn_id": "turn-1",
                "turns": [],
                "metadata": {
                    "user_speaker": "johnnyfiv3r",
                    "assistant_speaker": "assistant",
                },
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            beads_dir = Path(tmp) / ".beads"
            beads_dir.mkdir(parents=True)
            result = _resolve_envelope_speakers(tmp, envelopes, source_system="discord")

        self.assertEqual(len(result), 1)
        meta = result[0].get("metadata") or {}
        attribution = meta.get("speaker_attribution") or []
        self.assertGreater(len(attribution), 0)
        user_entry = next((e for e in attribution if e.get("role") == "user"), None)
        self.assertIsNotNone(user_entry)
        self.assertEqual(user_entry.get("speaker_observed"), "johnnyfiv3r")
        self.assertIsNotNone(user_entry.get("resolved_entity_id"))

    def test_resolve_envelope_speakers_no_speakers_returns_unchanged(self):
        from core_memory.transcript_ingest import _resolve_envelope_speakers

        envelopes = [
            {
                "session_id": "sess-1",
                "turn_id": "turn-1",
                "turns": [],
                "metadata": {},
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            result = _resolve_envelope_speakers(tmp, envelopes)

        self.assertEqual(result, envelopes)


class TestStoreAddBeadSpeakerAttribution(unittest.TestCase):
    def test_speaker_attribution_promotes_attributed_entity_id(self):
        """add_bead_for_store with speaker_attribution kwarg promotes attributed_entity_id."""
        import json
        import shutil
        import tempfile
        from pathlib import Path

        from core_memory.persistence.store import MemoryStore

        tmp = tempfile.mkdtemp()
        try:
            store = MemoryStore(tmp)
            bead_id = store.add_bead(
                type="context",
                title="Test turn",
                speaker_attribution={
                    "speaker_observed": "johnnyfiv3r",
                    "resolved_entity_id": "entity-abc123",
                    "resolution_confidence": 0.9,
                    "source_system": "discord",
                    "aliases": ["johnnyfiv3r"],
                    "resolved": True,
                },
            )
            self.assertTrue(bool(bead_id))

            idx = json.loads((Path(tmp) / ".beads" / "index.json").read_text())
            bead = idx["beads"][bead_id]
            self.assertEqual(bead.get("attributed_entity_id"), "entity-abc123")
            self.assertAlmostEqual(bead.get("resolution_confidence"), 0.9)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
