import tempfile
import unittest

from core_memory.persistence.store import MemoryStore


class TestStoreHygieneBehavior(unittest.TestCase):
    def test_sanitize_bead_content_redacts_secret_fields(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            bead = {
                "title": "Token github_pat_ABCDEF1234567890_ABCDEF1234567890 leaked",
                "summary": ["x-access-token:supersecrettokenvalue123456@github.com"],
                "detail": "",
                "because": [],
            }

            out = store._sanitize_bead_content(dict(bead))

            self.assertIn("[REDACTED_SECRET:github_pat:", out.get("title", ""))
            self.assertNotIn("ABCDEF1234567890_ABCDEF1234567890", out.get("title", ""))
            self.assertIn("[REDACTED_SECRET:x_access_token:", " ".join(out.get("summary", [])))
            self.assertNotIn("supersecrettokenvalue123456", " ".join(out.get("summary", [])))


if __name__ == "__main__":
    unittest.main()
