"""Backward-compat shim. Canonical location: core_memory.runtime.passes.agent_authored_contract."""
from core_memory.runtime.passes.agent_authored_contract import (  # noqa: F401
    ERROR_AGENT_UPDATES_MISSING,
    ERROR_AGENT_UPDATES_INVALID,
    ERROR_AGENT_ASSOCIATIONS_MISSING,
    ERROR_AGENT_BEAD_FIELDS_MISSING,
    ERROR_AGENT_INVOCATION_EXHAUSTED,
    ERROR_AGENT_CALLABLE_MISSING,
    ERROR_AGENT_SEMANTIC_COVERAGE_MISSING,
    AGENT_AUTHORED_REQUIRED_BEAD_FIELDS,
    AGENT_AUTHORED_REQUIRED_ASSOC_FIELDS,
    validate_agent_authored_updates,
    contract_snapshot,
)
