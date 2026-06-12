"""Example connector: GitHub as a system of record.

Maps GitHub webhook events into Core Memory's generic source-event ingest.
This is the reference implementation of the connector pattern — Jira,
HubSpot, and other systems of record follow the same shape: normalize the
provider event, declare which event kinds warrant beads, and let
`ingest_source_event` route the survivors through the typed external
evidence path (idempotency + source version supersession included).

Admission policy (default rules):

| GitHub event                          | warrants a bead?                          |
|---------------------------------------|-------------------------------------------|
| pull_request closed + merged          | yes — structured_observation (merge record) |
| pull_request closed unmerged / other  | no                                         |
| issues closed                         | yes — structured_observation (close record) |
| issues opened / labeled / assigned    | no                                         |
| release published                     | yes — document_reference (release notes)   |
| push to default branch touching *.md  | yes — document_reference per doc (versioned) |
| push elsewhere / other events         | no                                         |
| any event by a bot actor              | no                                         |

Like all adapters, this module consumes only the `core_memory` public API.
"""
from __future__ import annotations

from typing import Any

from core_memory import SourceEventMapping, SourceEventRule, ingest_source_event

DEFAULT_SESSION_ID = "github-source"

_DOC_SUFFIXES = (".md", ".mdx", ".rst", ".txt")


def _s(value: Any) -> str:
    return str(value or "").strip()


def _repo_full_name(event: dict[str, Any]) -> str:
    return _s((event.get("repository") or {}).get("full_name"))


def _actor_login(event: dict[str, Any]) -> str:
    return _s((event.get("sender") or {}).get("login"))


def _is_bot(event: dict[str, Any]) -> bool:
    sender = event.get("sender") or {}
    return _s(sender.get("type")).lower() == "bot" or _actor_login(event).endswith("[bot]")


def _delivery_id(event: dict[str, Any]) -> str:
    return _s(event.get("delivery_id") or event.get("x_github_delivery"))


def _base_fields(event: dict[str, Any], *, event_id: str, unifying_id: str, ref_url: str) -> dict[str, Any]:
    repo = _repo_full_name(event)
    return {
        "source_id": f"github:{repo}" if repo else "github",
        "source_event_id": event_id,
        "core_memory_unifying_id": unifying_id,
        "hydration_ref": {"store": "github", "ref": ref_url},
    }


def _build_merged_pull_request(event: dict[str, Any]) -> dict[str, Any] | None:
    if _is_bot(event):
        return None
    pr = event.get("pull_request") or {}
    if _s(event.get("action")) != "closed" or not bool(pr.get("merged")):
        return None
    repo = _repo_full_name(event)
    number = _s(pr.get("number"))
    author = _s((pr.get("user") or {}).get("login"))
    merged_at = _s(pr.get("merged_at"))
    title = _s(pr.get("title"))
    out = {
        "data_type_flag": "relational",
        "title": f"PR #{number} merged: {title}"[:200],
        "summary": [f"{author or 'unknown'} merged pull request #{number} in {repo}: {title}"],
        "detail": _s(pr.get("body"))[:1200],
        "source_table": "pull_requests",
        "source_record_id": f"{repo}#{number}",
        "record_action": "merged",
        "business_object_type": "pull_request",
        "business_object_id": f"{repo}#{number}",
        "entities": [x for x in (repo, author) if x],
        "entity_refs": [x for x in (repo, author) if x],
        "attribute_tags": ["github", "pull_request", "merged"],
        "observed_at": merged_at,
        "as_of_timestamp": merged_at,
    }
    out.update(_base_fields(
        event,
        event_id=_delivery_id(event) or f"github:{repo}:pr:{number}:merged",
        unifying_id=f"github:{repo}:pr:{number}",
        ref_url=_s(pr.get("html_url")),
    ))
    return out


def _build_closed_issue(event: dict[str, Any]) -> dict[str, Any] | None:
    if _is_bot(event):
        return None
    if _s(event.get("action")) != "closed":
        return None
    issue = event.get("issue") or {}
    repo = _repo_full_name(event)
    number = _s(issue.get("number"))
    author = _s((issue.get("user") or {}).get("login"))
    closed_at = _s(issue.get("closed_at"))
    title = _s(issue.get("title"))
    out = {
        "data_type_flag": "relational",
        "title": f"Issue #{number} closed: {title}"[:200],
        "summary": [f"Issue #{number} in {repo} closed: {title}"],
        "detail": _s(issue.get("body"))[:1200],
        "source_table": "issues",
        "source_record_id": f"{repo}#{number}",
        "record_action": "closed",
        "business_object_type": "issue",
        "business_object_id": f"{repo}#{number}",
        "entities": [x for x in (repo, author) if x],
        "entity_refs": [x for x in (repo, author) if x],
        "attribute_tags": ["github", "issue", "closed"],
        "observed_at": closed_at,
        "as_of_timestamp": closed_at,
    }
    out.update(_base_fields(
        event,
        event_id=_delivery_id(event) or f"github:{repo}:issue:{number}:closed",
        unifying_id=f"github:{repo}:issue:{number}",
        ref_url=_s(issue.get("html_url")),
    ))
    return out


def _build_published_release(event: dict[str, Any]) -> dict[str, Any] | None:
    if _s(event.get("action")) != "published":
        return None
    release = event.get("release") or {}
    repo = _repo_full_name(event)
    tag = _s(release.get("tag_name"))
    name = _s(release.get("name")) or tag
    out = {
        "data_type_flag": "document",
        "title": f"Release {name} published in {repo}"[:200],
        "summary": [f"Release {name} ({tag}) published in {repo}."],
        "detail": _s(release.get("body"))[:1200],
        "document_id": f"github:{repo}:release:{tag}",
        "document_name": f"{name} release notes",
        "document_kind": "release_notes",
        "document_date": _s(release.get("published_at")),
        "author_or_owner": _s((release.get("author") or {}).get("login")),
        "mime_type": "text/markdown",
        "entities": [x for x in (repo,) if x],
        "topics": ["release"],
        "observed_at": _s(release.get("published_at")),
    }
    out.update(_base_fields(
        event,
        event_id=_delivery_id(event) or f"github:{repo}:release:{tag}",
        unifying_id=f"github:{repo}:release:{tag}",
        ref_url=_s(release.get("html_url")),
    ))
    return out


def _build_default_branch_doc_push(event: dict[str, Any]) -> list[dict[str, Any]] | None:
    """One document_reference per documentation file changed on the default
    branch. Re-pushing the same file later versions the bead via the
    supersedes chain — the source's edit history becomes a version chain."""
    if _is_bot(event):
        return None
    repo_obj = event.get("repository") or {}
    repo = _s(repo_obj.get("full_name"))
    default_branch = _s(repo_obj.get("default_branch")) or "main"
    if _s(event.get("ref")) != f"refs/heads/{default_branch}":
        return None

    changed: dict[str, str] = {}
    for commit in (event.get("commits") or []):
        if not isinstance(commit, dict):
            continue
        cid = _s(commit.get("id"))
        for key in ("added", "modified"):
            for path in (commit.get(key) or []):
                p = _s(path)
                if p.lower().endswith(_DOC_SUFFIXES):
                    changed[p] = cid

    if not changed:
        return None

    head = _s(event.get("after"))
    pusher = _s((event.get("pusher") or {}).get("name"))
    payloads: list[dict[str, Any]] = []
    for path, commit_id in sorted(changed.items()):
        out = {
            "data_type_flag": "document",
            "title": f"{path} updated in {repo}"[:200],
            "summary": [f"{pusher or 'unknown'} updated {path} on {default_branch} ({(commit_id or head)[:12]})."],
            "document_id": f"github:{repo}:{path}",
            "document_name": path,
            "document_kind": "repository_doc",
            "author_or_owner": pusher,
            "mime_type": "text/markdown",
            "entities": [x for x in (repo, pusher) if x],
            "topics": ["documentation"],
            "observed_at": _s((event.get("head_commit") or {}).get("timestamp")),
        }
        out.update(_base_fields(
            event,
            # Per-file event identity: one push delivery may version several docs.
            event_id=f"{_delivery_id(event) or head}:{path}",
            unifying_id=f"github:{repo}:{path}",
            ref_url=f"https://github.com/{repo}/blob/{head or default_branch}/{path}" if repo else path,
        ))
        payloads.append(out)
    return payloads


DEFAULT_GITHUB_MAPPING = SourceEventMapping(
    source_system="github",
    rules=[
        SourceEventRule(
            name="merged_pull_request",
            event_kinds=("pull_request.closed", "pull_request"),
            build=_build_merged_pull_request,
            description="Merged PRs are records of change; closed-unmerged and other PR actions are noise.",
        ),
        SourceEventRule(
            name="closed_issue",
            event_kinds=("issues.closed", "issues"),
            build=_build_closed_issue,
            description="Issue closure is an outcome record; opens/labels/assigns are workflow noise.",
        ),
        SourceEventRule(
            name="published_release",
            event_kinds=("release.published", "release"),
            build=_build_published_release,
            description="Release notes are documents of record.",
        ),
        SourceEventRule(
            name="default_branch_doc_push",
            event_kinds=("push",),
            build=_build_default_branch_doc_push,
            description="Doc files changed on the default branch anchor as versioned document references.",
        ),
    ],
)


def ingest_github_event(
    root: str,
    *,
    event_name: str,
    event: dict[str, Any],
    delivery_id: str = "",
    session_id: str = DEFAULT_SESSION_ID,
    mapping: SourceEventMapping = DEFAULT_GITHUB_MAPPING,
) -> dict[str, Any]:
    """Ingest one GitHub webhook event.

    `event_name` is the X-GitHub-Event header value (e.g. "pull_request");
    the action inside the payload refines it to `event_kind`
    ("pull_request.closed"). `delivery_id` is the X-GitHub-Delivery GUID and
    becomes the idempotency key when provided.
    """
    payload = dict(event or {})
    if delivery_id:
        payload.setdefault("delivery_id", _s(delivery_id))
    action = _s(payload.get("action"))
    event_kind = f"{_s(event_name).lower()}.{action.lower()}" if action else _s(event_name).lower()
    return ingest_source_event(
        root,
        event_kind=event_kind,
        event=payload,
        mapping=mapping,
        session_id=session_id,
    )


__all__ = [
    "DEFAULT_GITHUB_MAPPING",
    "DEFAULT_SESSION_ID",
    "ingest_github_event",
]
