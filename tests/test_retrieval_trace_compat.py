from __future__ import annotations

import unittest
from unittest.mock import patch

from core_memory.retrieval.trace import trace_request


class TestRetrievalTraceCompat(unittest.TestCase):
    def test_trace_request_compat_delegates_to_canonical(self):
        with patch("core_memory.retrieval.trace._trace_request") as wrapped:
            wrapped.return_value = {"ok": True, "results": []}

            out = trace_request(root="/tmp/core-memory-test", query="why", anchor_ids=["bead-1"], k=3)

        self.assertEqual({"ok": True, "results": []}, out)
        wrapped.assert_called_once_with(
            root="/tmp/core-memory-test",
            query="why",
            anchor_ids=["bead-1"],
            k=3,
            intent="causal",
            hydration=None,
            submission=None,
            max_depth=None,
            max_chains=None,
        )


if __name__ == "__main__":
    unittest.main()
