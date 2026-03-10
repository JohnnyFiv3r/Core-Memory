import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.write_triggers import emit_write_trigger, dispatch_write_trigger


class TestWriteTriggerDispatch(unittest.TestCase):
    @patch('core_memory.write_triggers.subprocess.run')
    def test_dispatch_rolling_window(self, mrun):
        class P:
            returncode = 0
            stderr = ''
            stdout = ''
        mrun.return_value = P()

        with tempfile.TemporaryDirectory() as td:
            eid = emit_write_trigger(
                root=td,
                trigger_type='rolling_window_refresh',
                source='test',
                payload={'token_budget': 1234, 'max_beads': 12},
            )
            ev = {'event_id': eid, 'trigger_type': 'rolling_window_refresh', 'payload': {'token_budget': 1234, 'max_beads': 12}}
            out = dispatch_write_trigger(root=td, event=ev, workspace_root='/home/node/.openclaw/workspace')
            self.assertTrue(out.get('ok'))
            self.assertEqual(0, out.get('returncode'))
            self.assertTrue(mrun.called)
            cmd = mrun.call_args[0][0]
            self.assertIn('scripts/consolidate.py', ' '.join(cmd))
            self.assertIn('rolling-window', cmd)

    @patch('core_memory.write_triggers.subprocess.run')
    def test_dispatch_idempotent_processed_skip(self, mrun):
        class P:
            returncode = 0
            stderr = ''
            stdout = ''
        mrun.return_value = P()

        with tempfile.TemporaryDirectory() as td:
            ev = {'event_id': 'wtr-fixed', 'trigger_type': 'rolling_window_refresh', 'payload': {}}
            out1 = dispatch_write_trigger(root=td, event=ev, workspace_root='/home/node/.openclaw/workspace')
            out2 = dispatch_write_trigger(root=td, event=ev, workspace_root='/home/node/.openclaw/workspace')
            self.assertTrue(out1.get('ok'))
            self.assertTrue(out2.get('ok'))
            self.assertTrue(out2.get('skipped'))


if __name__ == '__main__':
    unittest.main()
