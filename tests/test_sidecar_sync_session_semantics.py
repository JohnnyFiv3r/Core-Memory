import importlib.util
import unittest
from pathlib import Path


def _load_module():
    p = Path(__file__).resolve().parents[1] / "scripts" / "sidecar_sync_session.py"
    spec = importlib.util.spec_from_file_location("sidecar_sync_session", str(p))
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


class TestSidecarSyncSessionSemantics(unittest.TestCase):
    def test_default_preserves_openclaw_session(self):
        m = _load_module()
        out = m.resolve_core_session_id(
            openclaw_session_id="sess-abc",
            core_session_id=None,
            collapse_to_main=False,
        )
        self.assertEqual("sess-abc", out)

    def test_collapse_to_main_compat_mode(self):
        m = _load_module()
        out = m.resolve_core_session_id(
            openclaw_session_id="sess-abc",
            core_session_id=None,
            collapse_to_main=True,
        )
        self.assertEqual("main", out)

    def test_explicit_core_session_id_override(self):
        m = _load_module()
        out = m.resolve_core_session_id(
            openclaw_session_id="sess-abc",
            core_session_id="custom-session",
            collapse_to_main=True,
        )
        self.assertEqual("custom-session", out)


if __name__ == "__main__":
    unittest.main()
