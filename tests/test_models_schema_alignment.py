import unittest

from core_memory.schema.models import BeadType, Status, RelationshipType
from core_memory.schema.normalization import CANONICAL_BEAD_TYPES, CANONICAL_BEAD_STATUSES, CANONICAL_RELATION_TYPES


class TestModelsSchemaAlignment(unittest.TestCase):
    def test_bead_type_alignment(self):
        model_vals = {x.value for x in BeadType}
        self.assertEqual(CANONICAL_BEAD_TYPES, model_vals)

    def test_status_alignment(self):
        model_vals = {x.value for x in Status}
        self.assertEqual(CANONICAL_BEAD_STATUSES, model_vals)

    def test_relation_alignment(self):
        model_vals = {x.value for x in RelationshipType}
        self.assertEqual(CANONICAL_RELATION_TYPES, model_vals)


if __name__ == "__main__":
    unittest.main()
