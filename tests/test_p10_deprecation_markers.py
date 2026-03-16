import unittest

import core_memory.runtime.trigger_pipeline as trigger_orch
import core_memory.association.pass_engine as pass_engine


class TestP10DeprecationMarkers(unittest.TestCase):
    def test_trigger_orchestrator_marked_as_legacy_shim(self):
        self.assertTrue(getattr(trigger_orch, "LEGACY_SHIM", False))
        self.assertEqual("core_memory.runtime.engine", getattr(trigger_orch, "SHIM_REPLACEMENT", ""))

    def test_association_pass_engine_marked_transitional(self):
        self.assertTrue(getattr(pass_engine, "DEPRECATED_PRIMARY", False))
        self.assertTrue(getattr(pass_engine, "NON_AUTHORITATIVE", False))
        self.assertEqual("core_memory.association.crawler_contract", getattr(pass_engine, "PRIMARY_REPLACEMENT", ""))


if __name__ == "__main__":
    unittest.main()
