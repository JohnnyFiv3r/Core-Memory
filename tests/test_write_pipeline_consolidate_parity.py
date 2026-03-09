import tempfile
import unittest
from pathlib import Path

from core_memory.store import MemoryStore
from core_memory.write_pipeline.consolidate import run_session_consolidation


class TestWritePipelineConsolidateParity(unittest.TestCase):
    def test_consolidate_output_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'memory'
            s = MemoryStore(str(root))
            s.add_bead(type='context', title='A', summary=['one'], session_id='main', source_turn_ids=['t1'])

            out = run_session_consolidation(
                root=str(root),
                workspace_root=td,
                session_id='main',
                promote=False,
                token_budget=500,
                max_beads=10,
            )
            self.assertTrue(out.get('ok'))
            for k in ['compaction', 'historical_compaction', 'rolling_window', 'included_bead_ids', 'excluded_bead_ids', 'written']:
                self.assertIn(k, out)
            self.assertTrue((Path(td) / 'promoted-context.md').exists())
            self.assertTrue((Path(td) / 'promoted-context.meta.json').exists())


if __name__ == '__main__':
    unittest.main()
