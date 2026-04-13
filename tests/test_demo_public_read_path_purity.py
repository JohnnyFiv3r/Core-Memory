import unittest
from pathlib import Path


class TestDemoPublicReadPathPurity(unittest.TestCase):
    def test_demo_app_read_path_avoids_private_store_inspection(self):
        p = Path(__file__).resolve().parent.parent / "demo" / "app.py"
        text = p.read_text(encoding="utf-8")

        forbidden = [
            "MemoryStore(",
            "_read_json(",
            "index.json",
            ".beads",
            ".turns",
            "resolve_all_current_state",
            "load_entity_registry",
            "semantic_doctor",
        ]

        hits = [tok for tok in forbidden if tok in text]
        self.assertEqual([], hits, msg=f"demo/app.py contains forbidden private read-path tokens: {hits}")


if __name__ == "__main__":
    unittest.main()
