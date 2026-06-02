"""SpringAI bridge app loader.

Wraps the canonical Core Memory HTTP app for SpringAI deployments.
This is an HTTP bridge — there is no native SpringAI runtime extension.
"""

ADAPTER_KIND = "http"
ADAPTER_RUNTIME = "springai"
ADAPTER_STATUS = "beta"

from core_memory.integrations.http import get_app as get_http_app


def get_app():
    return get_http_app()
