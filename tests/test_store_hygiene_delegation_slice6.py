import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreHygieneDelegationSlice6(unittest.TestCase):
    def test_sanitize_bead_content_delegates_to_hygiene_module(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            bead = {
                "title": "Token github_pat_ABCDEF1234567890_ABCDEF1234567890 leaked",
                "summary": ["x-access-token:supersecrettokenvalue123456@github.com"],
                "detail": "",
                "because": [],
            }

            def _stub_sanitize(payload):
                out = dict(payload)
                out["title"] = "sanitized-by-stub"
                return out

            with patch("core_memory.policy.hygiene.sanitize_bead_content", side_effect=_stub_sanitize) as stub:
                out = store._sanitize_bead_content(dict(bead))

            self.assertEqual(1, stub.call_count)
            self.assertEqual("sanitized-by-stub", out.get("title"))


if __name__ == "__main__":
    unittest.main()
