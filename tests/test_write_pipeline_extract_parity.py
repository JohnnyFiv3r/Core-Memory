import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.write_pipeline.marker_parse import extract_beads_from_transcript
from core_memory.write_pipeline.orchestrate import run_extract_pipeline


class TestWritePipelineExtractParity(unittest.TestCase):
    def _write_transcript(self, path: Path):
        lines = [
            {
                "role": "assistant",
                "content": "Hello <!--BEAD:{\"type\":\"promoted_lesson\",\"title\":\"Legacy\",\"summary\":[\"S\"],\"scope\":\"project\",\"authority\":\"agent\"}-->"
            },
            {
                "role": "assistant",
                "content": '{::bead type="decision" title="Attr style" summary="x | y" scope="project" authority="agent" /::}'
            },
        ]
        with path.open('w', encoding='utf-8') as f:
            for x in lines:
                f.write(json.dumps(x) + '\n')

    def test_marker_parser_supports_json_and_attr_styles(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 's1.jsonl'
            self._write_transcript(p)
            beads = extract_beads_from_transcript(p)
            self.assertEqual(2, len(beads))
            self.assertEqual('lesson', beads[0].get('type'))
            self.assertEqual('decision', beads[1].get('type'))

    @patch('core_memory.write_pipeline.orchestrate.write_beads_via_cli')
    @patch('core_memory.write_pipeline.orchestrate.find_transcript')
    def test_extract_pipeline_idempotent_skip(self, mfind, mwrite):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'memory'
            root.mkdir(parents=True, exist_ok=True)
            tr = Path(td) / 's1.jsonl'
            self._write_transcript(tr)
            mfind.return_value = (tr, 's1')
            mwrite.return_value = (2, 0)

            with patch('core_memory.write_pipeline.orchestrate.get_memory_root', return_value=str(root)):
                out1 = run_extract_pipeline(session_id='s1', consolidate=False)
                out2 = run_extract_pipeline(session_id='s1', consolidate=False)

            self.assertTrue(out1.get('ok'))
            self.assertEqual(2, out1.get('written'))
            self.assertTrue(out2.get('skipped'))


if __name__ == '__main__':
    unittest.main()
