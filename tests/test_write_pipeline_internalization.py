import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.write_pipeline.rolling_window import build_rolling_surface as build_rolling_window
from core_memory.write_pipeline.consolidate import run_rolling_window_refresh


class TestWritePipelineInternalization(unittest.TestCase):
    def test_rolling_window_internal_module(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type='context', title='A', summary=['one'], session_id='main', source_turn_ids=['t1'])
            text, meta, included, excluded = build_rolling_window(td, token_budget=500, max_beads=10)
            self.assertTrue(meta.get('selected') >= 1)
            self.assertTrue(isinstance(included, list))
            self.assertTrue(isinstance(excluded, list))
            self.assertTrue(isinstance(text, str))

    def test_rolling_window_pipeline_writes_promoted_context(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type='context', title='A', summary=['one'], session_id='main', source_turn_ids=['t1'])
            out = run_rolling_window_refresh(root=td, workspace_root=td, token_budget=500, max_beads=10)
            self.assertTrue(out.get('ok'))
            p = Path(td) / 'promoted-context.md'
            self.assertTrue(p.exists())


if __name__ == '__main__':
    unittest.main()
