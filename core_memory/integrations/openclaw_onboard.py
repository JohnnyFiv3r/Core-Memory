"""Backward-compat shim. Canonical location: core_memory.integrations.openclaw.onboard."""
from core_memory.integrations.openclaw.onboard import (  # noqa: F401
    run_openclaw_onboard,
    render_onboard_report,
)

if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run_openclaw_onboard(), indent=2))
