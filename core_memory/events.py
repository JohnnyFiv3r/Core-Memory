"""
Core-Memory event system.

This module provides event logging for audit and index rebuild.
Architecture: Index-first with event audit log.

Events are appended to .beads/events/ for:
- Audit trail of all state changes
- Ability to rebuild index from scratch
- Deterministic replay capability
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Iterator

from .io_utils import store_lock, append_jsonl, atomic_write_json

# Constants
EVENTS_DIR = ".beads/events"
SESSION_FILE = "session-{id}.jsonl"
METRICS_FILE = "metrics.jsonl"

# Event types
EVENT_BEAD_CREATED = "bead_created"
EVENT_BEAD_PROMOTED = "bead_promoted"
EVENT_BEAD_RECALLED = "bead_recalled"
EVENT_ASSOCIATION_CREATED = "association_created"
EVENT_BEAD_COMPACTED = "bead_compacted"
EVENT_BEAD_SUPERSEDED = "bead_superseded"
EVENT_EDGE_TRAVERSED = "edge_traversed"


def get_events_dir(root: Path) -> Path:
    """Get the events directory."""
    return root / EVENTS_DIR


def append_metric(root: Path, record: dict, use_lock: bool = True) -> None:
    """Append a metrics record to metrics.jsonl (append-only)."""
    events_dir = get_events_dir(root)
    events_dir.mkdir(parents=True, exist_ok=True)
    metric_file = events_dir / METRICS_FILE

    if use_lock:
        with store_lock(root):
            append_jsonl(metric_file, record)
    else:
        append_jsonl(metric_file, record)


def iter_metrics(root: Path) -> Iterator[dict]:
    """Iterate metrics records in deterministic order, skipping corrupt lines."""
    metric_file = get_events_dir(root) / METRICS_FILE
    if not metric_file.exists():
        return

    with open(metric_file, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def append_event(
    root: Path,
    session_id: Optional[str],
    event_type: str,
    payload: dict,
    use_lock: bool = True,
) -> str:
    """
    Append an event to the event log.
    
    Args:
        root: Root memory directory
        session_id: Associated session (can be None for global events)
        event_type: Type of event
        payload: Event data
        
    Returns:
        Event ID
    """
    events_dir = get_events_dir(root)
    events_dir.mkdir(parents=True, exist_ok=True)
    
    event_id = f"evt-{uuid.uuid4().hex[:12].upper()}"
    
    event = {
        "id": event_id,
        "event_type": event_type,
        "session_id": session_id,
        "payload": payload,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    # Write to session-specific or global events file
    if session_id:
        event_file = events_dir / SESSION_FILE.format(id=session_id)
    else:
        event_file = events_dir / "global.jsonl"
    
    if use_lock:
        with store_lock(root):
            append_jsonl(event_file, event)
    else:
        append_jsonl(event_file, event)
    
    return event_id


def iter_events(
    root: Path,
    session_id: Optional[str] = None
) -> Iterator[dict]:
    """
    Iterate over events.
    
    Args:
        root: Root memory directory
        session_id: Optional session filter
        
    Yields:
        Event dictionaries
    """
    events_dir = get_events_dir(root)
    
    if not events_dir.exists():
        return
    
    # Determine which files to read
    if session_id:
        files = [events_dir / SESSION_FILE.format(id=session_id)]
    else:
        # Read all event files (deterministic order)
        files = sorted(events_dir.glob("*.jsonl"))
    
    for event_file in files:
        if not event_file.exists():
            continue
        with open(event_file, 'r') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # tolerate partial/corrupt lines for durable replay
                    continue


def rebuild_index(root: Path) -> dict:
    """
    Rebuild the index from session JSONL archives.
    
    Since events are now minimal (just id + timestamp),
    we rebuild from the session JSONL files which contain full bead data.

    Note: transient index-only enrichment fields (e.g. association_preview)
    are not replayed from archives and may be recomputed later.
    
    Args:
        root: Root memory directory
        
    Returns:
        Rebuild index dictionary
    """
    from .persistence.store import SESSION_FILE, INDEX_FILE
    
    beads_dir = root / ".beads"
    index_file = beads_dir / INDEX_FILE
    
    # Initialize empty index
    index = {
        "beads": {},
        "associations": [],
        "stats": {
            "total_beads": 0,
            "total_associations": 0,
            "rebuilt_at": datetime.now(timezone.utc).isoformat()
        }
    }
    
    # Read all session JSONL files (archive layer, deterministic order)
    for session_file in sorted(beads_dir.glob("session-*.jsonl")):
        with open(session_file, 'r') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    bead = json.loads(line)
                except json.JSONDecodeError:
                    continue
                bead_id = bead.get("id")
                if bead_id:
                    index["beads"][bead_id] = bead
    
    # Also check global.jsonl
    global_file = beads_dir / "global.jsonl"
    if global_file.exists():
        with open(global_file, 'r') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    bead = json.loads(line)
                except json.JSONDecodeError:
                    continue
                bead_id = bead.get("id")
                if bead_id:
                    index["beads"][bead_id] = bead
    
    # Rebuild associations from event logs
    for ev in iter_events(root):
        if ev.get("event_type") == EVENT_ASSOCIATION_CREATED:
            assoc = (ev.get("payload") or {}).get("association")
            if assoc:
                index["associations"].append(assoc)

    # Deterministic ordering + de-dup by id where available
    dedup = {}
    for a in index["associations"]:
        key = a.get("id") or f"{a.get('source_bead')}->{a.get('target_bead')}:{a.get('relationship')}"
        dedup[key] = a
    index["associations"] = sorted(
        dedup.values(),
        key=lambda a: (a.get("created_at", ""), a.get("id", ""), a.get("source_bead", ""), a.get("target_bead", "")),
    )

    # Update stats
    index["stats"]["total_beads"] = len(index["beads"])
    index["stats"]["total_associations"] = len(index["associations"])

    # Write index atomically under store lock
    with store_lock(root):
        atomic_write_json(index_file, index)
    
    return index


# === Helper functions for store integration ===

def event_bead_created(
    root: Path,
    session_id: Optional[str],
    bead_id: str,
    created_at: str,
    use_lock: bool = True,
) -> str:
    """Create a bead_created event (minimal - just id + timestamp)."""
    return append_event(
        root=root,
        session_id=session_id,
        event_type=EVENT_BEAD_CREATED,
        payload={"bead_id": bead_id, "created_at": created_at},
        use_lock=use_lock,
    )


def event_bead_promoted(
    root: Path,
    bead_id: str,
    use_lock: bool = True,
) -> str:
    """Create a bead_promoted event."""
    return append_event(
        root=root,
        session_id=None,
        event_type=EVENT_BEAD_PROMOTED,
        payload={"bead_id": bead_id},
        use_lock=use_lock,
    )


def event_bead_recalled(
    root: Path,
    bead_id: str,
    use_lock: bool = True,
) -> str:
    """Create a bead_recalled event."""
    return append_event(
        root=root,
        session_id=None,
        event_type=EVENT_BEAD_RECALLED,
        payload={"bead_id": bead_id},
        use_lock=use_lock,
    )


def event_association_created(
    root: Path,
    association: dict,
    use_lock: bool = True,
) -> str:
    """Create an association_created event."""
    return append_event(
        root=root,
        session_id=None,
        event_type=EVENT_ASSOCIATION_CREATED,
        payload={"association": association},
        use_lock=use_lock,
    )


def event_edge_traversed(
    root: Path,
    edge_id: str,
    source_bead: str | None = None,
    target_bead: str | None = None,
    use_lock: bool = True,
) -> str:
    """Create an edge_traversed event for future reinforcement/decay modeling."""
    return append_event(
        root=root,
        session_id=None,
        event_type=EVENT_EDGE_TRAVERSED,
        payload={
            "edge_id": edge_id,
            "event": "traversed",
            "source_bead": source_bead,
            "target_bead": target_bead,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
        use_lock=use_lock,
    )
