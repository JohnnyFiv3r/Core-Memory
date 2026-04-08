from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreTextHygieneOpsDelegationSlice90A(unittest.TestCase):
    def test_text_and_hygiene_wrappers_delegate(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-text-deleg-") as td:
            s = MemoryStore(td)

            with patch("core_memory.persistence.store_text_hygiene_ops.tokenize_for_store", return_value={"a"}) as spy_t:
                self.assertEqual({"a"}, s._tokenize("hello"))
                self.assertEqual(1, spy_t.call_count)

            with patch("core_memory.persistence.store_text_hygiene_ops.is_memory_intent_for_store", return_value=True) as spy_i:
                self.assertTrue(s._is_memory_intent("remember this"))
                self.assertEqual(1, spy_i.call_count)

            with patch("core_memory.persistence.store_text_hygiene_ops.expand_query_tokens_for_store", return_value={"x"}) as spy_e:
                self.assertEqual({"x"}, s._expand_query_tokens("q", {"q"}, max_extra=5))
                self.assertEqual(1, spy_e.call_count)

            with patch("core_memory.persistence.store_text_hygiene_ops.redact_text_for_store", return_value="[redacted]") as spy_r:
                self.assertEqual("[redacted]", s._redact_text("secret"))
                self.assertEqual(1, spy_r.call_count)

            with patch("core_memory.persistence.store_text_hygiene_ops.sanitize_bead_content_for_store", return_value={"id": "b1"}) as spy_s:
                self.assertEqual({"id": "b1"}, s._sanitize_bead_content({"id": "b1"}))
                self.assertEqual(1, spy_s.call_count)

            with patch("core_memory.persistence.store_text_hygiene_ops.extract_constraints_for_store", return_value=["must do x"]) as spy_c:
                self.assertEqual(["must do x"], s.extract_constraints("must do x"))
                self.assertEqual(1, spy_c.call_count)


if __name__ == "__main__":
    unittest.main()
