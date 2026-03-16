import json
import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.write_trigger_dispatcher import emit_write_trigger


class TestWriteTriggers(unittest.TestCase):
    def test_emit_write_trigger_appends_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            eid = emit_write_trigger(
                root=td,
                trigger_type='extract_beads',
                source='extract-beads.py',
                payload={'session_id': 'abc'},
            )
            self.assertTrue(str(eid).startswith('wtr-'))
            p = Path(td) / '.beads' / 'events' / 'write-triggers.jsonl'
            self.assertTrue(p.exists())
            lines = p.read_text(encoding='utf-8').strip().splitlines()
            self.assertEqual(1, len(lines))
            rec = json.loads(lines[0])
            self.assertEqual('write_trigger', rec.get('kind'))
            self.assertEqual('extract_beads', rec.get('trigger_type'))
            self.assertEqual('extract-beads.py', rec.get('source'))


if __name__ == '__main__':
    unittest.main()
