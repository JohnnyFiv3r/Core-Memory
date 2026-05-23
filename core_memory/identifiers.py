from __future__ import annotations

import re
from typing import Any

ARCHIVE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def validate_archive_id(value: Any, *, field: str) -> str:
    """Validate session/turn identifiers used in filesystem-backed archives.

    These IDs are embedded in filenames. Reject path separators, traversal
    markers, empty strings, and shell-ish characters instead of normalizing so
    callers cannot accidentally alias distinct external IDs onto one file.
    """
    raw = str(value or "")
    if raw != raw.strip() or not ARCHIVE_ID_PATTERN.fullmatch(raw):
        raise ValueError(f"invalid_{field}")
    return raw
