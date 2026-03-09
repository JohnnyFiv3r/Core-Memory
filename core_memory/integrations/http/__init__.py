"""HTTP-compatible ingress surface.

Primary framing for integrations should use `core_memory.integrations.springai.get_app()`.
This module remains as compatibility ingress.
"""


def get_app():
    from .server import app

    return app


__all__ = ["get_app"]
