"""Backward-compat shim. Canonical location: core_memory.cli.handlers.setup."""
from core_memory.cli.handlers.setup import (  # noqa: F401
    init_command,
    doctor_command,
    expanded_doctor,
    config_show_command,
    config_set_command,
    config_validate_command,
    demo_command,
    _format_doctor_human,
    _cap_severity,
    _DEMO_SESSION,
)
