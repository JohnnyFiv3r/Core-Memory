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
    get_turn_tool,
    get_turn_tools_tool,
    get_adjacent_turns_tool,
    hydrate_bead_sources_tool,
    CONTINUITY_HEADER,
)
from core_memory.runtime.state import TurnEnvelope, emit_memory_event
from core_memory.persistence.store import MemoryStore
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


class TestHydrationTools(unittest.TestCase):
    def test_turn_hydration_tools(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            _make_store(root)

            env = TurnEnvelope(
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="u",
                assistant_final="a",
                tools_trace=[{"tool_call_id": "1", "category": "search", "result_hash": "h"}],
            )
            emit_memory_event(Path(root), env)

            turn = json.loads(get_turn_tool(root=root)("t1", "s1"))
            self.assertTrue(turn["found"])
            self.assertEqual("t1", turn["turn"]["turn_id"])

            tools = json.loads(get_turn_tools_tool(root=root)("t1", "s1"))
            self.assertTrue(tools["found"])
            self.assertEqual("search", tools["tools_trace"][0]["category"])

            neighbors = json.loads(get_adjacent_turns_tool(root=root)("t1", "s1", 1, 1))
            self.assertTrue(neighbors["found"])
            self.assertEqual("t1", neighbors["pivot"]["turn_id"])

    def test_hydrate_bead_sources_tool(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            _make_store(root)
            env = TurnEnvelope(
                session_id="s1",
                turn_id="t2",
                transaction_id="tx2",
                trace_id="tr2",
                user_query="u2",
                assistant_final="a2",
            )
            emit_memory_event(Path(root), env)

            s = MemoryStore(root=root)
            bead_id = s.add_bead(
                type="context",
                title="x",
                summary=["y"],
                source_turn_ids=["t2"],
                detail="z",
                session_id="s1",
            )

            tool = hydrate_bead_sources_tool(root=root)
            out = json.loads(tool(bead_ids_json=json.dumps([bead_id]), include_tools=False, before=0, after=0))
            self.assertIn("hydrated", out)
            self.assertEqual("t2", out["hydrated"][0]["turn"]["turn_id"])


class TestImportsFromInit(unittest.TestCase):
    def test_all_surfaces_importable(self):
        from core_memory.integrations.pydanticai import (
            continuity_prompt,
            memory_search_tool,
            memory_reason_tool,
            memory_execute_tool,
            get_turn_tool,
            get_turn_tools_tool,
            get_adjacent_turns_tool,
            hydrate_bead_sources_tool,
        )
        self.assertTrue(callable(continuity_prompt))
        self.assertTrue(callable(memory_search_tool))
        self.assertTrue(callable(memory_reason_tool))
        self.assertTrue(callable(memory_execute_tool))
        self.assertTrue(callable(get_turn_tool))
        self.assertTrue(callable(get_turn_tools_tool))
        self.assertTrue(callable(get_adjacent_turns_tool))
        self.assertTrue(callable(hydrate_bead_sources_tool))


if __name__ == "__main__":
    unittest.main()
