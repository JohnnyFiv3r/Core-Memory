"""Backward-compat shim. Canonical location: core_memory.cli.compat."""
from core_memory.cli.compat import (  # noqa: F401
    rewrite_legacy_dev_memory_argv,
    ensure_group_subcommand_selected,
    apply_grouped_aliases,
)
