#!/usr/bin/env python3
"""Deprecated mem_beads CLI shim.

Routes all invocations to core_memory. Kept for migration compatibility.
"""

import sys


def main() -> int:
    from core_memory.adapter_cli import can_handle_with_core_adapter, run_core_adapter

    # preserve command name for compatibility output/help
    sys.argv[0] = "mem-beads"
    print(
        "[deprecation] `mem_beads` module shim is deprecated; use `core-memory` CLI.",
        file=sys.stderr,
    )

    if not can_handle_with_core_adapter(sys.argv):
        raise SystemExit("Unsupported mem-beads command in compatibility shim")

    return run_core_adapter(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
