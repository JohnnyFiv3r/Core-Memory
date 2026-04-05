from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core_memory.integrations.openclaw_flags import (
    agent_authored_fail_open_enabled,
    agent_authored_required_enabled,
    runtime_flags_snapshot,
)
from core_memory.runtime.agent_authored_contract import contract_snapshot


class TestAgentAuthoredContractSlice0(unittest.TestCase):
    def test_flags_default_false(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORE_MEMORY_AGENT_AUTHORED_REQUIRED", None)
            os.environ.pop("CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN", None)
            self.assertFalse(agent_authored_required_enabled())
            self.assertFalse(agent_authored_fail_open_enabled())

    def test_flags_parse_true_values(self):
        with patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "true",
            },
            clear=False,
        ):
            self.assertTrue(agent_authored_required_enabled())
            self.assertTrue(agent_authored_fail_open_enabled())

    def test_runtime_snapshot_includes_agent_authored_flags(self):
        snap = runtime_flags_snapshot()
        self.assertIn("agent_authored_required_enabled", snap)
        self.assertIn("agent_authored_fail_open_enabled", snap)

    def test_contract_snapshot_lists_error_codes_and_required_fields(self):
        snap = contract_snapshot()
        errs = set(snap.get("error_codes") or [])
        self.assertIn("agent_updates_missing", errs)
        self.assertIn("agent_updates_invalid", errs)
        self.assertIn("agent_associations_missing", errs)
        self.assertIn("agent_bead_fields_missing", errs)

        bead_required = set(snap.get("required_bead_fields") or [])
        self.assertTrue({"type", "title", "summary"}.issubset(bead_required))


if __name__ == "__main__":
    unittest.main()
