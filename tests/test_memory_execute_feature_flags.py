import os
import tempfile
import unittest

from core_memory.store import MemoryStore
from core_memory.tools.memory import execute


class TestMemoryExecuteFeatureFlags(unittest.TestCase):
    def test_global_disable_flag(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type='decision', title='X', summary=['Y'], session_id='main', source_turn_ids=['t1'])
            old = os.environ.get('MEMORY_EXECUTE_ENABLED')
            os.environ['MEMORY_EXECUTE_ENABLED'] = '0'
            try:
                out = execute({'raw_query': 'remember x', 'intent': 'remember'}, root=td, explain=False)
                self.assertFalse(out.get('ok'))
                self.assertEqual('memory_execute_disabled', out.get('error'))
                self.assertEqual('memory_execute_result.v1', out.get('schema_version'))
                self.assertEqual('memory_execute', out.get('contract'))
            finally:
                if old is None:
                    os.environ.pop('MEMORY_EXECUTE_ENABLED', None)
                else:
                    os.environ['MEMORY_EXECUTE_ENABLED'] = old


if __name__ == '__main__':
    unittest.main()
