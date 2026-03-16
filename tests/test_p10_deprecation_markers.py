import unittest

import core_memory.association.pass_engine as pass_engine


class TestP10DeprecationMarkers(unittest.TestCase):
    def test_association_pass_engine_marked_transitional(self):
        self.assertTrue(getattr(pass_engine, "DEPRECATED_PRIMARY", False))
        self.assertTrue(getattr(pass_engine, "NON_AUTHORITATIVE", False))
        self.assertEqual("core_memory.association.crawler_contract", getattr(pass_engine, "PRIMARY_REPLACEMENT", ""))


if __name__ == "__main__":
    unittest.main()
