import unittest
from pathlib import Path


class TestStoreBoundaryContract(unittest.TestCase):
    def test_store_does_not_import_runtime_engine_or_trigger_shims(self):
        root = Path(__file__).resolve().parents[1]
        store_py = root / "core_memory" / "store.py"
        text = store_py.read_text(encoding="utf-8")

        forbidden = [
            "core_memory.runtime.engine",
            "from .runtime.engine import",
            "trigger_orchestrator",
            "write_triggers",
        ]
        offenders = [x for x in forbidden if x in text]
        self.assertEqual([], offenders, msg=f"store.py violated boundary contract: {offenders}")


if __name__ == "__main__":
    unittest.main()
