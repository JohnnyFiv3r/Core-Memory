"""F-W2 acceptance tests: configurable agent-authored gate severity.

Verifies:
1. Gate severity accepts hard|warn|off values.
2. Default is warn (not observe/off).
3. Legacy aliases: enforce→hard, observe→off.
4. Resolved gate returns correct required/fail_open for each mode.
"""

import os
import unittest
from unittest.mock import patch

from core_memory.integrations.openclaw_flags import (
    agent_authored_mode,
    resolved_agent_authored_gate,
)


class TestGateSeverityValues(unittest.TestCase):
    """Gate accepts hard|warn|off and legacy aliases."""

    @patch.dict(os.environ, {"CORE_MEMORY_AGENT_AUTHORED_MODE": "hard"}, clear=False)
    def test_hard_mode(self):
        self.assertEqual(agent_authored_mode(), "hard")

    @patch.dict(os.environ, {"CORE_MEMORY_AGENT_AUTHORED_MODE": "warn"}, clear=False)
    def test_warn_mode(self):
        self.assertEqual(agent_authored_mode(), "warn")

    @patch.dict(os.environ, {"CORE_MEMORY_AGENT_AUTHORED_MODE": "off"}, clear=False)
    def test_off_mode(self):
        self.assertEqual(agent_authored_mode(), "off")

    @patch.dict(os.environ, {"CORE_MEMORY_AGENT_AUTHORED_MODE": "enforce"}, clear=False)
    def test_legacy_enforce_maps_to_hard(self):
        self.assertEqual(agent_authored_mode(), "hard")

    @patch.dict(os.environ, {"CORE_MEMORY_AGENT_AUTHORED_MODE": "observe"}, clear=False)
    def test_legacy_observe_maps_to_off(self):
        self.assertEqual(agent_authored_mode(), "off")


class TestDefaultIsWarn(unittest.TestCase):
    """Default gate severity is warn in OSS."""

    def test_default_is_warn(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORE_MEMORY_AGENT_AUTHORED_MODE", None)
            os.environ.pop("CORE_MEMORY_AGENT_AUTHORED_REQUIRED", None)
            os.environ.pop("CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN", None)
            self.assertEqual(agent_authored_mode(), "warn")


class TestResolvedGate(unittest.TestCase):
    """Resolved gate dict has correct flags for each mode."""

    @patch.dict(os.environ, {"CORE_MEMORY_AGENT_AUTHORED_MODE": "hard"}, clear=False)
    def test_hard_gate(self):
        gate = resolved_agent_authored_gate()
        self.assertEqual(gate["mode"], "hard")
        self.assertTrue(gate["required"])
        self.assertFalse(gate["fail_open"])

    @patch.dict(os.environ, {"CORE_MEMORY_AGENT_AUTHORED_MODE": "warn"}, clear=False)
    def test_warn_gate(self):
        gate = resolved_agent_authored_gate()
        self.assertEqual(gate["mode"], "warn")
        self.assertTrue(gate["required"])
        self.assertTrue(gate["fail_open"])

    @patch.dict(os.environ, {"CORE_MEMORY_AGENT_AUTHORED_MODE": "off"}, clear=False)
    def test_off_gate(self):
        gate = resolved_agent_authored_gate()
        self.assertEqual(gate["mode"], "off")
        self.assertFalse(gate["required"])
        self.assertTrue(gate["fail_open"])


if __name__ == "__main__":
    unittest.main()
