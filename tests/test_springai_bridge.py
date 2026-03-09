import unittest

from core_memory.integrations.springai import get_app


class TestSpringAIBridge(unittest.TestCase):
    def test_get_app_returns_fastapi(self):
        app = get_app()
        self.assertTrue(hasattr(app, "routes"))
        self.assertIn("SpringAI Bridge", getattr(app, "title", ""))


if __name__ == "__main__":
    unittest.main()
