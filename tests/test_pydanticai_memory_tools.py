import json
import os
import tempfile
import unittest
from pathlib import Path

from core_memory.integrations.pydanticai.memory_tools import (
    continuity_prompt,
    memory_search_tool,
    memory_reason_tool,
    memory_execute_tool,
    CONTINUITY_HEADER,
)
from core_memory.persistence.rolling_record_store import write_rolling_records


def _make_store(root: str) -> None:
    """Bootstrap a minimal .beads directory so tools don't error on missing store."""
    beads_dir = Path(root) / ".beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    events_dir = beads_dir / "events"
    events_dir.mkdir(exist_ok=True)
    index = {
        "beads": {},
        "associations": [],
        "stats": {"total_beads": 0, "total_associations": 0},
        "projection": {"mode": "session_first_projection_cache", "rebuilt_at": None},
    }
    (beads_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")


def _seed_rolling_window(root: str, records: list[dict]) -> None:
    """Write rolling-window records so continuity injection has data."""
    write_rolling_records(
        root,
        records=records,
        meta={"source": "test"},
        included_bead_ids=[r.get("id", f"b-{i}") for i, r in enumerate(records)],
        excluded_bead_ids=[],
    )


class TestContinuityPrompt(unittest.TestCase):
    def test_empty_store_returns_empty_string(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            Path(root).mkdir()
            result = continuity_prompt(root=root)
            self.assertEqual(result, "")

    def test_returns_formatted_records(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            Path(root).mkdir()
            _seed_rolling_window(root, [
                {"id": "b-1", "title": "Chose PostgreSQL", "summary": "JSONB support was key", "type": "decision"},
                {"id": "b-2", "title": "Auth refactor", "summary": ["compliance", "legal requirement"], "type": "goal"},
            ])

            result = continuity_prompt(root=root)

            self.assertIn(CONTINUITY_HEADER.strip(), result)
            self.assertIn("Chose PostgreSQL", result)
            self.assertIn("JSONB support was key", result)
            self.assertIn("Auth refactor", result)
            self.assertIn("compliance legal requirement", result)
            self.assertIn("2 record(s)", result)

    def test_respects_max_items(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            Path(root).mkdir()
            records = [{"id": f"b-{i}", "title": f"Item {i}", "summary": f"Summary {i}"} for i in range(20)]
            _seed_rolling_window(root, records)

            result = continuity_prompt(root=root, max_items=5)

            self.assertIn("5 record(s)", result)
            self.assertIn("Item 0", result)
            self.assertNotIn("Item 5", result)

    def test_resolves_root_from_env(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            Path(root).mkdir()
            _seed_rolling_window(root, [
                {"id": "b-1", "title": "EnvTest", "summary": "via env"},
            ])
            os.environ["CORE_MEMORY_ROOT"] = root
            try:
                result = continuity_prompt()
                self.assertIn("EnvTest", result)
            finally:
                os.environ.pop("CORE_MEMORY_ROOT", None)


class TestMemorySearchTool(unittest.TestCase):
    def test_returns_callable(self):
        tool = memory_search_tool(root="/tmp/nonexistent")
        self.assertTrue(callable(tool))
        self.assertEqual(tool.__name__, "search_memory")

    def test_search_returns_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            _make_store(root)
            tool = memory_search_tool(root=root)
            result = tool("test query")
            parsed = json.loads(result)
            self.assertIn("results", parsed)

    def test_search_with_empty_store_returns_no_matches(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            _make_store(root)
            tool = memory_search_tool(root=root)
            result = json.loads(tool("nonexistent topic"))
            self.assertEqual(result["results"], [])


class TestMemoryReasonTool(unittest.TestCase):
    def test_returns_callable(self):
        tool = memory_reason_tool(root="/tmp/nonexistent")
        self.assertTrue(callable(tool))
        self.assertEqual(tool.__name__, "reason_about_memory")

    def test_reason_returns_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            _make_store(root)
            tool = memory_reason_tool(root=root)
            result = tool("why did we choose X?")
            parsed = json.loads(result)
            self.assertIsInstance(parsed, dict)


class TestMemoryExecuteTool(unittest.TestCase):
    def test_returns_callable(self):
        tool = memory_execute_tool(root="/tmp/nonexistent")
        self.assertTrue(callable(tool))
        self.assertEqual(tool.__name__, "execute_memory_request")

    def test_execute_returns_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            _make_store(root)
            tool = memory_execute_tool(root=root)
            result = tool("test query", intent="search")
            parsed = json.loads(result)
            self.assertIsInstance(parsed, dict)


class TestImportsFromInit(unittest.TestCase):
    def test_all_surfaces_importable(self):
        from core_memory.integrations.pydanticai import (
            continuity_prompt,
            memory_search_tool,
            memory_reason_tool,
            memory_execute_tool,
        )
        self.assertTrue(callable(continuity_prompt))
        self.assertTrue(callable(memory_search_tool))
        self.assertTrue(callable(memory_reason_tool))
        self.assertTrue(callable(memory_execute_tool))


if __name__ == "__main__":
    unittest.main()
