from __future__ import annotations

import enum
import inspect
import unittest

import core_memory.schema.models as models


class TestSchemaStringEnumsSlice51B(unittest.TestCase):
    def test_all_schema_enums_are_string_enums(self):
        enum_classes = [
            obj
            for _, obj in vars(models).items()
            if inspect.isclass(obj)
            and issubclass(obj, enum.Enum)
            and obj.__module__ == models.__name__
        ]
        self.assertGreaterEqual(len(enum_classes), 1)
        for cls in enum_classes:
            self.assertTrue(issubclass(cls, str), f"{cls.__name__} should inherit from str, Enum")

    def test_enum_members_compare_naturally_to_strings(self):
        enum_classes = [
            obj
            for _, obj in vars(models).items()
            if inspect.isclass(obj)
            and issubclass(obj, enum.Enum)
            and obj.__module__ == models.__name__
        ]
        for cls in enum_classes:
            for member in cls:
                self.assertEqual(member.value, str(member.value))
                self.assertTrue(member == member.value, f"{cls.__name__}.{member.name} should compare equal to its string value")


if __name__ == "__main__":
    unittest.main()
