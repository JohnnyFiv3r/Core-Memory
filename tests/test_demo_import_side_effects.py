import os
import unittest


class TestDemoImportSideEffects(unittest.TestCase):
    def test_importing_demo_app_does_not_mutate_auto_promote_env(self):
        key = "CORE_MEMORY_AUTO_PROMOTE_ON_COMPACT"
        old = os.environ.get(key)
        try:
            os.environ.pop(key, None)
            import importlib
            import demo.app as demo_app

            importlib.reload(demo_app)
            self.assertIsNone(os.environ.get(key))
        finally:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


if __name__ == "__main__":
    unittest.main()
