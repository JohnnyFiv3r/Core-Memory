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

# Constants
EVENTS_DIR = ".beads/events"
SESSION_FILE = "session-{id}.jsonl"

# Event types
EVENT_BEAD_CREATED = "bead_created"
EVENT_BEAD_PROMOTED = "bead_promoted"
EVENT_BEAD_RECALLED = "bead_recalled"
EVENT_ASSOCIATION_CREATED = "association_created"
EVENT_BEAD_COMPACTED = "bead_compacted"
EVENT_BEAD_SUPERSEDED = "bead_superseded"


def get_events_dir(root: Path) -> Path:
    """Get the events directory."""
    return root / EVENTS_DIR


def append_event(
    root: Path,
    session_id: Optional[str],
    event_type: str,
    payload: dict
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
    
    with open(event_file, 'a') as f:
        f.write(json.dumps(event) + "\n")
    
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
        # Read all event files
        files = list(events_dir.glob("*.jsonl"))
    
    for event_file in files:
        if not event_file.exists():
            continue
        with open(event_file, 'r') as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def rebuild_index(root: Path) -> dict:
    """
    Rebuild the index from all events.
    
    This is the canonical way to ensure index consistency.
    
    Args:
        root: Root memory directory
        
    Returns:
        Rebuild index dictionary
    """
    from .store import SESSION_FILE, INDEX_FILE
    
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
    
    # Process all events
    for event in iter_events(root):
        event_type = event.get("event_type")
        payload = event.get("payload", {})
        
        if event_type == EVENT_BEAD_CREATED:
            bead = payload.get("bead", {})
            if bead.get("id"):
                index["beads"][bead["id"]] = bead
        
        elif event_type == EVENT_BEAD_PROMOTED:
            bead_id = payload.get("bead_id")
            if bead_id and bead_id in index["beads"]:
                index["beads"][bead_id]["status"] = "promoted"
                index["beads"][bead_id]["promoted_at"] = event.get("created_at")
        
        elif event_type == EVENT_BEAD_RECALLED:
            bead_id = payload.get("bead_id")
            if bead_id and bead_id in index["beads"]:
                bead = index["beads"][bead_id]
                bead["recall_count"] = bead.get("recall_count", 0) + 1
                bead["last_recalled"] = event.get("created_at")
        
        elif event_type == EVENT_ASSOCIATION_CREATED:
            # Don't duplicate - associations are in their own file
            index["stats"]["total_associations"] = index["stats"].get("total_associations", 0) + 1
    
    # Update stats
    index["stats"]["total_beads"] = len(index["beads"])
    
    # Write index
    with open(index_file, 'w') as f:
        json.dump(index, f, indent=2)
    
    return index


# === Helper functions for store integration ===

def event_bead_created(
    root: Path,
    session_id: Optional[str],
    bead: dict
) -> str:
    """Create a bead_created event."""
    return append_event(
        root=root,
        session_id=session_id,
        event_type=EVENT_BEAD_CREATED,
        payload={"bead": bead}
    )


def event_bead_promoted(
    root: Path,
    bead_id: str
) -> str:
    """Create a bead_promoted event."""
    return append_event(
        root=root,
        session_id=None,
        event_type=EVENT_BEAD_PROMOTED,
        payload={"bead_id": bead_id}
    )


def event_bead_recalled(
    root: Path,
    bead_id: str
) -> str:
    """Create a bead_recalled event."""
    return append_event(
        root=root,
        session_id=None,
        event_type=EVENT_BEAD_RECALLED,
        payload={"bead_id": bead_id}
    )


def event_association_created(
    root: Path,
    association: dict
) -> str:
    """Create an association_created event."""
    return append_event(
        root=root,
        session_id=None,
        event_type=EVENT_ASSOCIATION_CREATED,
        payload={"association": association}
    )
