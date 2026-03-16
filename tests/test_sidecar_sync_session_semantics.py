import unittest

from core_memory.integrations.openclaw_runtime import resolve_core_session_id


class TestSidecarSyncSessionSemantics(unittest.TestCase):
    def test_default_preserves_openclaw_session(self):
        out = resolve_core_session_id(
            openclaw_session_id="sess-abc",
            core_session_id=None,
            collapse_to_main=False,
        )
        self.assertEqual("sess-abc", out)

    def test_collapse_to_main_compat_mode(self):
        out = resolve_core_session_id(
            openclaw_session_id="sess-abc",
            core_session_id=None,
            collapse_to_main=True,
        )
        self.assertEqual("main", out)

    def test_explicit_core_session_id_override(self):
        out = resolve_core_session_id(
            openclaw_session_id="sess-abc",
            core_session_id="custom-session",
            collapse_to_main=True,
        )
        self.assertEqual("custom-session", out)


if __name__ == "__main__":
    unittest.main()
