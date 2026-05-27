"""Backward-compat shim. Canonical location: core_memory.integrations.openclaw.read_bridge."""
from core_memory.integrations.openclaw.read_bridge import (  # noqa: F401
    dispatch,
    main,
)

if __name__ == "__main__":
    main()
