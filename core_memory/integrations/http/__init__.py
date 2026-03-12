"""HTTP-compatible ingress surface.

Primary framing for integrations should use `core_memory.integrations.springai.get_app()`.
This module remains as compatibility ingress.
"""


def get_app():
    try:
        from .server import app
        return app
    except ModuleNotFoundError as exc:
        if str(getattr(exc, "name", "")) != "fastapi":
            raise

        class _FallbackApp:
            title = "SpringAI Bridge (fallback)"
            routes = []

        return _FallbackApp()


__all__ = ["get_app"]
