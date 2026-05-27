"""Backward-compat shim. Canonical location: core_memory.integrations.openclaw.agent_end_bridge."""
from core_memory.integrations.openclaw.agent_end_bridge import (  # noqa: F401
    ADAPTER_KIND,
    ADAPTER_RUNTIME,
    ADAPTER_STATUS,
    process_agent_end_event,
    main,
)

if __name__ == "__main__":
    main()
