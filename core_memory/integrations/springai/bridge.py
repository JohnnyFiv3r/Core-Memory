"""SpringAI bridge app loader.

Primary framing: SpringAI native HTTP integration surface.
Compatibility: reuses canonical HTTP ingress implementation.
"""

ADAPTER_KIND = "native"
ADAPTER_RUNTIME = "springai"
ADAPTER_STATUS = "production_ready"

from core_memory.integrations.http import get_app as get_http_app


def get_app():
    return get_http_app()
