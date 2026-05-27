"""Backward-compat shim. Canonical location: core_memory.integrations.openclaw.compaction_bridge."""
from core_memory.integrations.openclaw.compaction_bridge import (  # noqa: F401
    process_compaction_event,
    main,
)

if __name__ == "__main__":
    main()
