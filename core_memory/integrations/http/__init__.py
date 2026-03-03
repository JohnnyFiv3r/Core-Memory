def get_app():
    from .server import app

    return app


__all__ = ["get_app"]
