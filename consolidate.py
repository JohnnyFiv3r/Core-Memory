#!/usr/bin/env python3
"""Core Memory consolidation utility (compatibility wrapper).

Canonical implementation moved to: `scripts/consolidate.py`.
This root entrypoint is retained temporarily for operational compatibility.
"""

import warnings

from scripts.consolidate import main


if __name__ == "__main__":
    warnings.warn(
        "consolidate.py moved: use scripts/consolidate.py (root wrapper retained for compatibility)",
        DeprecationWarning,
        stacklevel=1,
    )
    main()
