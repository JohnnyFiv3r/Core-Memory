"""SpringAI bridge integration entrypoints.

This package provides SpringAI-first framing over the existing HTTP ingress
implementation while preserving compatibility with generic HTTP deployment.
"""

from .bridge import get_app

__all__ = ["get_app"]
