#!/usr/bin/env python3
"""CLI entry point for mem-beads package.

Phase 2 migration behavior:
- default: existing mem_beads implementation
- opt-in adapter: route to core_memory via MEMBEADS_USE_CORE_ADAPTER=1
"""

import os
import sys


def main() -> int:
    use_core = os.environ.get("MEMBEADS_USE_CORE_ADAPTER", "0") == "1"

    if use_core:
        from core_memory.adapter_cli import run_core_adapter

        # preserve command name
        sys.argv[0] = "mem-beads"
        return run_core_adapter(sys.argv)

    # legacy default behavior
    from mem_beads import main as legacy_main

    sys.argv[0] = "mem-beads"
    legacy_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
