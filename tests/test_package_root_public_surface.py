import unittest

import core_memory
from core_memory.runtime import engine as runtime_engine
from core_memory.retrieval.tools import memory as memory_tools


class TestPackageRootPublicSurface(unittest.TestCase):
    def test_root_exports_canonical_runtime_functions(self):
        self.assertIs(core_memory.process_turn_finalized, runtime_engine.process_turn_finalized)
        self.assertIs(core_memory.process_session_start, runtime_engine.process_session_start)
        self.assertIs(core_memory.process_flush, runtime_engine.process_flush)
        self.assertIs(core_memory.emit_turn_finalized, runtime_engine.emit_turn_finalized)

    def test_root_exports_canonical_retrieval_functions(self):
        self.assertIs(core_memory.memory_search, memory_tools.search)
        self.assertIs(core_memory.memory_trace, memory_tools.trace)
        self.assertIs(core_memory.memory_execute, memory_tools.execute)

    def test_all_includes_canonical_surface(self):
        for symbol in [
            "process_turn_finalized",
            "process_session_start",
            "process_flush",
            "emit_turn_finalized",
            "memory_search",
            "memory_trace",
            "memory_execute",
        ]:
            self.assertIn(symbol, core_memory.__all__)


if __name__ == "__main__":
    unittest.main()
