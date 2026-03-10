import unittest

import core_memory.integrations.openclaw_agent_end_bridge as openclaw_bridge
import core_memory.integrations.springai.bridge as springai_bridge
import core_memory.integrations.pydanticai.run as pyd_run


class TestAdapterContractMarkers(unittest.TestCase):
    def test_openclaw_bridge_markers(self):
        self.assertEqual("bridge", getattr(openclaw_bridge, "ADAPTER_KIND", ""))
        self.assertEqual("openclaw", getattr(openclaw_bridge, "ADAPTER_RUNTIME", ""))

    def test_springai_markers(self):
        self.assertEqual("native", getattr(springai_bridge, "ADAPTER_KIND", ""))
        self.assertEqual("springai", getattr(springai_bridge, "ADAPTER_RUNTIME", ""))

    def test_pydantic_markers(self):
        self.assertEqual("native", getattr(pyd_run, "ADAPTER_KIND", ""))
        self.assertEqual("pydanticai", getattr(pyd_run, "ADAPTER_RUNTIME", ""))


if __name__ == "__main__":
    unittest.main()
