import json
import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.lexical import lexical_lookup, LexicalIndex, _CACHE_VERSION
from core_memory.persistence.store import MemoryStore


class TestLexicalCacheVersion(unittest.TestCase):
    def test_stale_cache_is_rejected_and_rebuilt(self):
        """A cache file with no version (or wrong version) must be ignored."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads_dir = root / ".beads"
            beads_dir.mkdir()
            cache_path = beads_dir / "lexical_cache.json"
            # Write a stale v1 cache (no version field)
            stale = {"docs": [{"bead_id": "stale-id", "type": "context", "status": "open", "tokens": ["stale"]}],
                     "df": {"stale": 1}}
            cache_path.write_text(json.dumps(stale), encoding="utf-8")

            idx = LexicalIndex(root)
            loaded = idx._load_cache()
            self.assertFalse(loaded, "stale cache without version should be rejected")

    def test_valid_versioned_cache_is_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads_dir = root / ".beads"
            beads_dir.mkdir()
            cache_path = beads_dir / "lexical_cache.json"
            valid = {"version": _CACHE_VERSION,
                     "docs": [{"bead_id": "v2-id", "type": "context", "status": "open", "tokens": ["hello"]}],
                     "df": {"hello": 1}}
            cache_path.write_text(json.dumps(valid), encoding="utf-8")

            idx = LexicalIndex(root)
            loaded = idx._load_cache()
            self.assertTrue(loaded)
            self.assertIn("v2-id", idx._bead_ids)

    def test_saved_cache_has_current_version(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s = MemoryStore(td)
            s.add_bead(type="context", title="version test", session_id="s1", source_turn_ids=["t1"])
            idx = LexicalIndex(root)
            idx.build()
            cache_path = root / ".beads" / "lexical_cache.json"
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("version"), _CACHE_VERSION)


class TestLexicalFieldWeights(unittest.TestCase):
    def test_title_and_tag_weighting_surfaces_anchor(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="context", title="graph archive retrieval", summary=["misc note"], tags=["graph_archive_retrieval"], session_id="main", source_turn_ids=["t1"])
            b = s.add_bead(type="context", title="misc", summary=["graph archive retrieval details"], session_id="main", source_turn_ids=["t2"])
            out = lexical_lookup(Path(td), "graph archive retrieval", k=5)
            ids = [r.get("bead_id") for r in (out.get("results") or [])]
            self.assertTrue(ids)
            self.assertEqual(a, ids[0])
            self.assertIn(b, ids)


if __name__ == "__main__":
    unittest.main()
