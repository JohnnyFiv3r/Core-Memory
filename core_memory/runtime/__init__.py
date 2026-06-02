"""Runtime namespace package for canonical turn/flush execution modules.

The package __init__ is intentionally empty: submodules (engine.py, state.py,
turn/, flush/, etc.) are heavy and rarely all needed at once, so callers
import submodules directly (e.g., `from core_memory.runtime.engine import ...`)
to keep import-time cost low.

This is a lazy-load optimization, not a circular-import workaround.
"""
