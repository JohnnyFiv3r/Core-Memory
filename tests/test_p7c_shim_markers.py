import unittest

import core_memory.memory_skill.form as form_shim
import core_memory.write_pipeline.window as window_shim


class TestP7CShimMarkers(unittest.TestCase):
    def test_shim_markers_present(self):
        self.assertTrue(getattr(form_shim, "LEGACY_SHIM", False))
        self.assertEqual("core_memory.retrieval.search_form", getattr(form_shim, "SHIM_REPLACEMENT", ""))

        self.assertTrue(getattr(window_shim, "LEGACY_SHIM", False))
        self.assertEqual("core_memory.rolling_surface", getattr(window_shim, "SHIM_REPLACEMENT", ""))


if __name__ == "__main__":
    unittest.main()
