"""Core Memory 2 is a command, not a Python library.

Version 1 of this distribution was a Python library imported as
``core_memory``. Version 2 is an engine written in Rust; installing it puts
the ``core-memory`` command on the path, and nothing here is importable.
A program written against the 1.x library would otherwise fail later and
less clearly, so the import fails now and says what to do.
"""

raise ImportError(
    "core-memory 2 installs the `core-memory` command, not a Python library. "
    "Run `core-memory --help`. For the 1.x Python library, pin "
    '"core-memory<2" (the last 1.x release is 1.1.2).'
)
