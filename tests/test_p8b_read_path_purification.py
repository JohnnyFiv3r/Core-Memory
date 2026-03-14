import unittest
from pathlib import Path


class TestP8BReadPathPurification(unittest.TestCase):
    def test_continuity_surface_file_reads_are_canonicalized(self):
        root = Path(__file__).resolve().parents[1]
        core = root / "core_memory"

        target_literals = [
            "rolling-window.records.json",
            "promoted-context.meta.json",
            "promoted-context.md",
        ]

        allowed_paths = {
            core / "continuity_injection.py",
            core / "persistence" / "rolling_record_store.py",
            core / "write_pipeline" / "rolling_window.py",
        }

        offenders: list[str] = []
        for py in core.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if any(lit in text for lit in target_literals):
                if py not in allowed_paths:
                    offenders.append(str(py.relative_to(root)))

        self.assertEqual([], offenders, msg=f"Found non-canonical continuity surface accessors: {offenders}")


if __name__ == "__main__":
    unittest.main()
