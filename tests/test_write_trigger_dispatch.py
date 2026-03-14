import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.runtime.write_trigger_dispatcher import emit_write_trigger, dispatch_write_trigger


class TestWriteTriggerDispatch(unittest.TestCase):
    def test_dispatch_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            eid = emit_write_trigger(
                root=td,
                trigger_type='rolling_window_refresh',
                source='test',
                payload={'token_budget': 1234, 'max_beads': 12},
            )
            ev = {'event_id': eid, 'trigger_type': 'rolling_window_refresh', 'payload': {'token_budget': 1234, 'max_beads': 12}}
            out = dispatch_write_trigger(root=td, event=ev, workspace_root=td)
            self.assertFalse(out.get('ok'))
            self.assertEqual('legacy_write_triggers_disabled', out.get('error'))

    @patch('core_memory.write_pipeline.orchestrate.run_rolling_window_pipeline')
    def test_dispatch_rolling_window_when_enabled(self, mrun):
        mrun.return_value = {'ok': True, 'rolling_window': {'selected': 0}}
        with tempfile.TemporaryDirectory() as td:
            os.environ['CORE_MEMORY_ALLOW_LEGACY_WRITE_TRIGGERS'] = '1'
            try:
                ev = {'event_id': 'wtr-fixed', 'trigger_type': 'rolling_window_refresh', 'payload': {'token_budget': 1234, 'max_beads': 12}}
                out = dispatch_write_trigger(root=td, event=ev, workspace_root=td)
                self.assertTrue(out.get('ok'))
                self.assertTrue(mrun.called)
            finally:
                os.environ.pop('CORE_MEMORY_ALLOW_LEGACY_WRITE_TRIGGERS', None)


if __name__ == '__main__':
    unittest.main()
