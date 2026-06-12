"""Generic ingest path for events from external systems of record.

Systems of record (GitHub, Jira, HubSpot, ...) emit far more events than
memory warrants. This module is the admission layer between a connector's
event stream and the typed external-evidence write path: a connector
normalizes its webhook/poll payloads into source-event envelopes, a rule set
decides which events are bead-worthy, and only those produce beads — via
`ingest_external_evidence`, which supplies idempotency and source version
supersession.

Bead-worthiness here is event-level admission policy (declarative rules),
not agent judgment. Associations between the resulting beads and the rest of
memory remain agent-judged at agent_end, per the architectural invariant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from core_memory.runtime.ingest.external_evidence import ingest_external_evidence


@dataclass
class SourceEventRule:
    """One admission rule: which event kinds it matches and how to build
    the external-evidence payload(s) for them.

    `build` returns one payload dict, a list of payload dicts (an event may
    anchor several records, e.g. one per changed document), or None when the
    matched event still does not warrant a bead.
    """

    name: str
    event_kinds: tuple[str, ...]
    build: Callable[[dict[str, Any]], dict[str, Any] | list[dict[str, Any]] | None]
    description: str = ""

    def matches(self, event_kind: str) -> bool:
        kind = str(event_kind or "").strip().lower()
        for pattern in self.event_kinds:
            p = str(pattern or "").strip().lower()
            if not p:
                continue
            if p.endswith("*"):
                if kind.startswith(p[:-1]):
                    return True
            elif kind == p:
                return True
        return False


@dataclass
class SourceEventMapping:
    """A connector's complete admission policy: ordered rules, first match wins."""

    source_system: str
    rules: list[SourceEventRule] = field(default_factory=list)

    def rule_for(self, event_kind: str) -> SourceEventRule | None:
        for rule in self.rules:
            if rule.matches(event_kind):
                return rule
        return None


def _skip_receipt(event_kind: str, reason: str) -> dict[str, Any]:
    return {
        "ok": True,
        "accepted": False,
        "status": "skipped",
        "mode": "source_event",
        "event_kind": str(event_kind or ""),
        "reason": reason,
        "bead_ids": [],
        "created_count": 0,
    }


def ingest_source_event(
    root: str,
    *,
    event_kind: str,
    event: dict[str, Any],
    mapping: SourceEventMapping,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate one system-of-record event against a connector's mapping.

    Returns a skip receipt (with reason) when the event does not warrant a
    bead, otherwise the aggregated external-evidence receipts. Each emitted
    payload inherits the mapping's source_system unless the builder set one.
    """
    if not isinstance(event, dict):
        raise ValueError("source_event: event must be an object")

    rule = mapping.rule_for(event_kind)
    if rule is None:
        return _skip_receipt(event_kind, "event_not_bead_worthy")

    built = rule.build(dict(event))
    if built is None:
        return _skip_receipt(event_kind, f"rule_declined:{rule.name}")
    payloads = built if isinstance(built, list) else [built]
    payloads = [dict(p) for p in payloads if isinstance(p, dict) and p]
    if not payloads:
        return _skip_receipt(event_kind, f"rule_declined:{rule.name}")

    receipts: list[dict[str, Any]] = []
    for payload in payloads:
        payload.setdefault("source_system", mapping.source_system)
        receipts.append(ingest_external_evidence(root, payload, session_id=session_id))

    statuses = {str(r.get("status") or "") for r in receipts}
    if "version_superseded" in statuses:
        status = "version_superseded"
    elif "accepted" in statuses:
        status = "accepted"
    else:
        status = "already_exists"
    return {
        "ok": all(bool(r.get("ok")) for r in receipts),
        "accepted": True,
        "status": status,
        "mode": "source_event",
        "event_kind": str(event_kind or ""),
        "rule": rule.name,
        "bead_ids": [str(r.get("bead_id") or "") for r in receipts if r.get("bead_id")],
        "created_count": sum(int(r.get("created_count") or 0) for r in receipts),
        "receipts": receipts,
    }


__all__ = [
    "SourceEventMapping",
    "SourceEventRule",
    "ingest_source_event",
]
