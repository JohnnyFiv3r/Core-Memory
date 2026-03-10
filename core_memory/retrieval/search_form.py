from __future__ import annotations

SEARCH_FORM_SCHEMA_VERSION = "memory_search_form.v1"
SEARCH_FORM_TOOL_ID = "core.memory_search"


def get_search_form(catalog: dict) -> dict:
    return {
        "schema_version": SEARCH_FORM_SCHEMA_VERSION,
        "tool": SEARCH_FORM_TOOL_ID,
        "fields": {
            "intent": {"type": "enum", "allowed": ["remember", "causal", "what_changed", "when", "other"], "required": True},
            "query_text": {"type": "string", "required": True},
            "incident_id": {"type": "string", "required": False},
            "topic_keys": {"type": "list[string]", "max_items": 3, "required": False},
            "bead_types": {"type": "list[enum]", "max_items": 3, "required": False},
            "relation_types": {"type": "list[enum]", "max_items": 3, "required": False},
            "time_range": {"type": "object", "required": False, "shape": {"from": "iso8601", "to": "iso8601"}},
            "must_terms": {"type": "list[string]", "max_items": 5, "required": False},
            "avoid_terms": {"type": "list[string]", "max_items": 5, "required": False},
            "k": {"type": "int", "min": 1, "max": 30, "default": 10},
            "require_structural": {"type": "bool", "default": False},
        },
        "catalog": catalog,
    }
