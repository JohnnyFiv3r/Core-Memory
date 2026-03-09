"""SpringAI bridge app loader.

Primary framing: SpringAI bridge ingress.
Compatibility: reuses canonical HTTP ingress implementation.
"""

from core_memory.integrations.http import get_app as get_http_app


def get_app():
    return get_http_app()
