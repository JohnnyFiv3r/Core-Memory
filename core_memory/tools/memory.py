from __future__ import annotations

from core_memory.memory_skill import memory_get_search_form, memory_search_typed, memory_execute
from core_memory.tools.memory_reason import memory_reason


def get_search_form(root: str = "./memory") -> dict:
    return memory_get_search_form(root)


def search(form_submission: dict, root: str = "./memory", explain: bool = True) -> dict:
    return memory_search_typed(root=root, submission=form_submission, explain=bool(explain))


def reason(
    query: str,
    root: str = "./memory",
    k: int = 8,
    debug: bool = False,
    explain: bool = False,
    pinned_incident_ids: list[str] | None = None,
    pinned_topic_keys: list[str] | None = None,
    pinned_bead_ids: list[str] | None = None,
) -> dict:
    return memory_reason(
        query=query,
        root=root,
        k=int(k),
        debug=bool(debug),
        explain=bool(explain),
        pinned_incident_ids=pinned_incident_ids,
        pinned_topic_keys=pinned_topic_keys,
        pinned_bead_ids=pinned_bead_ids,
    )


def execute(request: dict, root: str = "./memory", explain: bool = True) -> dict:
    return memory_execute(root=root, request=request, explain=bool(explain))
