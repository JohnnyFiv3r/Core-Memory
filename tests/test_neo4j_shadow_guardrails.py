import builtins
import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.engine import process_turn_finalized, process_flush
from core_memory.retrieval.tools.memory import execute as memory_execute


class _BlockNeo4jImport:
    def __enter__(self):
        self._orig = builtins.__import__

        def _guard(name, globals=None, locals=None, fromlist=(), level=0):
            n = str(name or "")
            if n == "neo4j" or n.startswith("neo4j."):
                raise AssertionError("canonical runtime imported neo4j")
            return self._orig(name, globals, locals, fromlist, level)

        builtins.__import__ = _guard
        return self

    def __exit__(self, exc_type, exc, tb):
        builtins.__import__ = self._orig
        return False


class TestNeo4jShadowGuardrails(unittest.TestCase):
    def test_canonical_runtime_paths_do_not_require_neo4j_import(self):
        with _BlockNeo4jImport(), tempfile.TemporaryDirectory() as td:
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="why",
                assistant_final="because",
            )
            self.assertTrue(out.get("ok"))

            fl = process_flush(
                root=td,
                session_id="s1",
                source="test",
                promote=True,
                token_budget=800,
                max_beads=12,
            )
            self.assertTrue(fl.get("ok"))

            ex = memory_execute(
                request={"raw_query": "why", "intent": "remember", "k": 5},
                root=td,
                explain=True,
            )
            self.assertIn("ok", ex)

    def test_core_canonical_modules_have_no_neo4j_dependency_strings(self):
        root = Path(__file__).resolve().parents[1]
        targets = [
            root / "core_memory" / "runtime" / "engine.py",
            root / "core_memory" / "write_pipeline" / "continuity_injection.py",
            root / "core_memory" / "retrieval" / "pipeline" / "canonical.py",
            root / "core_memory" / "retrieval" / "tools" / "memory.py",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("neo4j", text, f"canonical module references neo4j: {path}")


if __name__ == "__main__":
    unittest.main()
