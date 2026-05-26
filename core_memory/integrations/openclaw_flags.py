"""Backward-compat shim. Canonical location: core_memory.integrations.openclaw.flags.

Phase 9c moved all OpenClaw integration files into integrations/openclaw/.
This shim re-exports everything so existing callers continue to work.
"""
from core_memory.integrations.openclaw.flags import *  # noqa: F401, F403
