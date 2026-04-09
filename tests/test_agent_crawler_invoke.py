from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core_memory.runtime.agent_crawler_invoke import invoke_turn_crawler_agent
import agent_crawler_fixtures


class TestAgentCrawlerInvokeSlice3(unittest.TestCase):
    def _req(self):
        return {
            "session_id": "s1",
            "turn_id": "t1",
            "user_query": "q",
            "assistant_final": "a",
            "metadata": {},
        }

    def test_disabled_when_not_required_and_no_callable(self):
        with patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "0",
                "CORE_MEMORY_AGENT_CRAWLER_INVOKE": "0",
            },
            clear=False,
        ):
            updates, diag = invoke_turn_crawler_agent(root="/tmp", req=self._req(), crawler_context={})
            self.assertIsNone(updates)
            self.assertFalse(diag.get("attempted"))
            self.assertEqual("invocation_disabled", diag.get("reason"))

    def test_missing_callable_reports_deterministic_error(self):
        with patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_CRAWLER_CALLABLE": "",
            },
            clear=False,
        ):
            updates, diag = invoke_turn_crawler_agent(root="/tmp", req=self._req(), crawler_context={})
            self.assertIsNone(updates)
            self.assertTrue(diag.get("attempted"))
            self.assertEqual("agent_callable_missing", diag.get("error_code"))

    def test_invalid_callable_configuration_does_not_raise(self):
        with patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_CRAWLER_CALLABLE": "not_a_module:not_a_fn",
            },
            clear=False,
        ):
            updates, diag = invoke_turn_crawler_agent(root="/tmp", req=self._req(), crawler_context={})
            self.assertIsNone(updates)
            self.assertTrue(diag.get("attempted"))
            self.assertEqual("agent_callable_missing", diag.get("error_code"))

    def test_retry_then_success(self):
        agent_crawler_fixtures.reset_state()
        with patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_CRAWLER_CALLABLE": "agent_crawler_fixtures:fail_once_then_success",
                "CORE_MEMORY_AGENT_CRAWLER_MAX_ATTEMPTS": "3",
            },
            clear=False,
        ):
            updates, diag = invoke_turn_crawler_agent(root="/tmp", req=self._req(), crawler_context={})
            self.assertIsInstance(updates, dict)
            self.assertTrue(diag.get("ok"))
            self.assertEqual(2, int(diag.get("attempts") or 0))

    def test_exhaustion_reports_error_code(self):
        agent_crawler_fixtures.reset_state()
        with patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_CRAWLER_CALLABLE": "agent_crawler_fixtures:always_fail",
                "CORE_MEMORY_AGENT_CRAWLER_MAX_ATTEMPTS": "2",
            },
            clear=False,
        ):
            updates, diag = invoke_turn_crawler_agent(root="/tmp", req=self._req(), crawler_context={})
            self.assertIsNone(updates)
            self.assertTrue(diag.get("attempted"))
            self.assertEqual(2, int(diag.get("attempts") or 0))
            self.assertEqual("agent_invocation_exhausted", diag.get("error_code"))


if __name__ == "__main__":
    unittest.main()
