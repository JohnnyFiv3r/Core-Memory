import tempfile
import unittest

from core_memory.policy.incidents import tag_topic_key
from core_memory.persistence.store import MemoryStore


class TestTopicKeyTagging(unittest.TestCase):
    def test_tag_topic_key_adds_tag(self):
        with tempfile.TemporaryDirectory() as td:
            m = MemoryStore(root=td)
            bid = m.add_bead(type="context", title="retrieval hardening pass", summary=["did work"])
            out = tag_topic_key(m.root, "graph_archive_retrieval", [bid])
            self.assertTrue(out.get("ok"))
            idx = m._read_json(m.beads_dir / "index.json")
            b = (idx.get("beads") or {}).get(bid) or {}
            self.assertIn("graph_archive_retrieval", [str(t) for t in (b.get("tags") or [])])


if __name__ == "__main__":
    unittest.main()
