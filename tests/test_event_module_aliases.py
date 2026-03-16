import unittest

from core_memory.runtime import state as event_state
from core_memory.runtime import ingress as event_ingress
from core_memory.runtime import worker as event_worker


class TestEventModuleAliases(unittest.TestCase):
    def test_event_state_exports_pass_state_functions(self):
        self.assertTrue(hasattr(event_state, "try_claim_memory_pass"))
        self.assertTrue(hasattr(event_state, "mark_memory_pass"))

    def test_event_ingress_exports_finalize_hook(self):
        self.assertTrue(hasattr(event_ingress, "maybe_emit_finalize_memory_event"))

    def test_event_worker_exports_policy_and_processor(self):
        self.assertTrue(hasattr(event_worker, "SidecarPolicy"))
        self.assertTrue(hasattr(event_worker, "process_memory_event"))


if __name__ == "__main__":
    unittest.main()
