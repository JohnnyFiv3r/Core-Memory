import json
import unittest
from pathlib import Path

import core_memory
from core_memory.runtime import engine as runtime_engine
from core_memory.retrieval.tools import memory as memory_tools


class TestPackageRootPublicSurface(unittest.TestCase):
    def test_root_exports_canonical_runtime_functions(self):
        self.assertIs(core_memory.process_turn_finalized, runtime_engine.process_turn_finalized)
        self.assertTrue(callable(core_memory.capture))
        self.assertIs(core_memory.process_session_start, runtime_engine.process_session_start)
        self.assertIs(core_memory.process_flush, runtime_engine.process_flush)
        self.assertIs(core_memory.emit_turn_finalized, runtime_engine.emit_turn_finalized)

    def test_root_exports_canonical_retrieval_functions(self):
        self.assertIs(core_memory.memory_search, memory_tools.search)
        self.assertIs(core_memory.memory_trace, memory_tools.trace)
        self.assertIs(core_memory.memory_execute, memory_tools.execute)

    def test_all_includes_canonical_surface(self):
        for symbol in [
            "Memory",
            "Turn",
            "capture",
            "recall",
            "process_turn_finalized",
            "process_session_start",
            "process_flush",
            "emit_turn_finalized",
            "memory_search",
            "memory_trace",
            "memory_execute",
        ]:
            self.assertIn(symbol, core_memory.__all__)


class TestPackagedDataFiles(unittest.TestCase):
    """Guards against the wheel omitting core_memory/data/*.json (regression for A1/A2)."""

    def _data_dir(self) -> Path:
        import core_memory as _cm
        return Path(_cm.__file__).parent / "data"

    def test_data_dir_exists_and_contains_json_files(self):
        d = self._data_dir()
        self.assertTrue(d.is_dir(), f"core_memory/data/ not found at {d}")
        json_files = list(d.glob("*.json"))
        self.assertGreater(len(json_files), 0, "core_memory/data/ contains no .json files")

    def test_incidents_json_loads(self):
        p = self._data_dir() / "incidents.json"
        self.assertTrue(p.exists(), f"incidents.json missing: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertIsInstance(data, list, "incidents.json must be a JSON array")

    def test_structural_relation_map_json_loads(self):
        p = self._data_dir() / "structural_relation_map.json"
        self.assertTrue(p.exists(), f"structural_relation_map.json missing: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict, "structural_relation_map.json must be a JSON object")
        self.assertGreater(len(data), 0, "structural_relation_map.json must be non-empty")

    def test_topic_aliases_json_loads(self):
        p = self._data_dir() / "topic_aliases.json"
        self.assertTrue(p.exists(), f"topic_aliases.json missing: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertIsInstance(data, (dict, list), "topic_aliases.json must be valid JSON")

    def test_relation_map_path_resolves_to_existing_file(self):
        """_relation_map_path() must point at the real data file, not core_memory/graph/data/."""
        from core_memory.graph.core import _relation_map_path, _load_structural_relation_map
        p = _relation_map_path()
        self.assertTrue(p.exists(), f"_relation_map_path() returned non-existent path: {p}")
        # Confirm it's under core_memory/data/, not the non-existent core_memory/graph/data/
        self.assertIn("core_memory/data", str(p).replace("\\", "/"),
                      f"_relation_map_path() should be under core_memory/data/, got {p}")
        rel_map = _load_structural_relation_map()
        self.assertIsInstance(rel_map, dict)
        self.assertGreater(len(rel_map), 0, "structural relation map loaded empty")


if __name__ == "__main__":
    unittest.main()
