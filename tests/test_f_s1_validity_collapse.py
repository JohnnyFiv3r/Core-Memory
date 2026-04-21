"""F-S1 acceptance tests: collapse validity into status.

Verifies:
1. Status enum includes TRANSIENT.
2. Retrieval scoring checks only status, not validity.
3. validity=closed migrates to status=archived in normalization.
4. validity=superseded migrates to status=superseded.
5. validity=transient migrates to status=transient.
6. validity field is deprecated but still accepted (no error).
"""

import unittest

from core_memory.schema.models import Bead, Status, _normalize_bead_payload


class TestStatusEnumHasTransient(unittest.TestCase):
    """Status enum includes transient value from F-S1."""

    def test_transient_value_exists(self):
        self.assertEqual(Status.TRANSIENT.value, "transient")

    def test_all_expected_values(self):
        values = {s.value for s in Status}
        for expected in ["open", "candidate", "promoted", "compacted", "superseded", "archived", "transient"]:
            self.assertIn(expected, values)


class TestValidityToStatusMigration(unittest.TestCase):
    """Bead normalization migrates validity → status."""

    def test_validity_closed_becomes_archived(self):
        raw = {"type": "decision", "title": "test", "validity": "closed"}
        out = _normalize_bead_payload(raw)
        self.assertEqual(out["status"], "archived")

    def test_validity_superseded_becomes_superseded(self):
        raw = {"type": "decision", "title": "test", "validity": "superseded"}
        out = _normalize_bead_payload(raw)
        self.assertEqual(out["status"], "superseded")

    def test_validity_transient_becomes_transient(self):
        raw = {"type": "decision", "title": "test", "validity": "transient"}
        out = _normalize_bead_payload(raw)
        self.assertEqual(out["status"], "transient")

    def test_validity_open_no_migration(self):
        raw = {"type": "decision", "title": "test", "validity": "open"}
        out = _normalize_bead_payload(raw)
        # validity=open doesn't override; status defaults to open via normal path
        self.assertEqual(out["status"], "open")

    def test_explicit_status_wins_over_validity(self):
        raw = {"type": "decision", "title": "test", "status": "promoted", "validity": "closed"}
        out = _normalize_bead_payload(raw)
        # explicit status takes precedence — validity migration only fires when status is empty
        self.assertEqual(out["status"], "promoted")

    def test_no_validity_no_migration(self):
        raw = {"type": "decision", "title": "test"}
        out = _normalize_bead_payload(raw)
        self.assertEqual(out["status"], "open")


class TestValidityFieldDeprecated(unittest.TestCase):
    """validity field is accepted but not used for scoring."""

    def test_validity_field_preserved_in_normalized_payload(self):
        raw = {
            "type": "decision", "title": "test",
            "validity": "transient",
        }
        out = _normalize_bead_payload(raw)
        # Field is preserved for backward compat
        self.assertEqual(out.get("validity"), "transient")
        # Status should be set via migration
        self.assertEqual(out["status"], "transient")


class TestScoringUsesStatusOnly(unittest.TestCase):
    """Evidence scoring checks status, not validity."""

    def test_evidence_scoring_checks_status_not_validity(self):
        """Verify the source code uses status, not validity, for supersession penalty."""
        import inspect
        from core_memory.retrieval import evidence_scoring
        source = inspect.getsource(evidence_scoring)
        # The old pattern was: validity = str(bead.get("validity")...)
        self.assertNotIn('bead.get("validity")', source)
        # The new pattern checks status
        self.assertIn('bead.get("status")', source)


if __name__ == "__main__":
    unittest.main()
