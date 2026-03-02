"""
Core-Memory store implementation.

This module contains the MemoryStore class which handles all persistence.
Index-first with event audit log:
- index.json is primary (fast queries)
- Events provide audit trail and rebuild capability
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import BeadType, Scope, Status, Authority
from . import events

# Defaults for pip package (separate from live OpenClaw usage)
DEFAULT_ROOT = "./memory"
BEADS_DIR = ".beads"
TURNS_DIR = ".turns"
EVENTS_DIR = ".beads/events"
SESSION_FILE = "session-{id}.jsonl"
INDEX_FILE = "index.json"

# NOTE: Write-order tradeoff
# For performance, we write to index.json first, then append events.
# In rare crash windows between these operations, index may reflect
# state not yet present in events. rebuild_index() can reconcile.
# This is a deliberate performance tradeoff vs true event-sourcing.


class MemoryStore:
    """
    Persistent causal agent memory with lossless compaction.
    Index-first with event audit log:
    - index.json is the primary source of truth (fast queries)
    - Events are appended to .beads/events/ for audit/rebuild
    
    Usage:
        memory = MemoryStore(root="./memory")
        memory.capture_turn(role="assistant", content="...")
        memory.consolidate(session_id="chat-123")
    """
    
    def __init__(self, root: str = DEFAULT_ROOT):
        """Initialize MemoryStore at the given root directory."""
        self.root = Path(root)
        self.beads_dir = self.root / BEADS_DIR
        self.turns_dir = self.root / TURNS_DIR
        
        # Ensure directories exist
        self.beads_dir.mkdir(parents=True, exist_ok=True)
        self.turns_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize index if needed
        self._init_index()
    
    def _init_index(self):
        """Initialize the index file if it doesn't exist."""
        index_file = self.beads_dir / INDEX_FILE
        if not index_file.exists():
            self._write_json(index_file, {
                "beads": {},
                "associations": [],
                "stats": {
                    "total_beads": 0,
                    "total_associations": 0,
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
            })
    
    def _read_json(self, path: Path) -> dict:
        """Read a JSON file."""
        with open(path, 'r') as f:
            return json.load(f)
    
    def _write_json(self, path: Path, data: dict):
        """Write a JSON file."""
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
    
    def _generate_id(self) -> str:
        """Generate a ULID-style ID."""
        return f"bead-{uuid.uuid4().hex[:12].upper()}"
    
    # === Core API ===
    
    def add_bead(
        self,
        type: str,
        title: str,
        summary: Optional[list] = None,
        detail: str = "",
        session_id: Optional[str] = None,
        scope: str = "project",
        tags: Optional[list] = None,
        links: Optional[dict] = None,
        **kwargs
    ) -> str:
        """
        Create a new bead.
        
        Args:
            type: Bead type (BeadType enum or string)
            title: Short descriptive title
            summary: List of key points
            detail: Full narrative (preserved in archive)
            session_id: Associated session
            scope: Scope (Scope enum or string)
            tags: List of tags
            links: Causal/associative links
            
        Returns:
            Bead ID
        """
        from .models import BeadType, Scope
        
        # Normalize enums to strings
        type_value = self._normalize_enum(type, BeadType)
        scope_value = self._normalize_enum(scope, Scope)
        bead_id = self._generate_id()
        now = datetime.now(timezone.utc).isoformat()
        
        bead = {
            "id": bead_id,
            "type": type_value,
            "created_at": now,
            "session_id": session_id,
            "title": title,
            "summary": summary or [],
            "detail": detail,
            "scope": scope_value,
            "authority": "agent_inferred",
            "confidence": 0.8,
            "tags": tags or [],
            "links": links or {},
            "status": "open",
            "recall_count": 0,
            "last_recalled": None,
            **kwargs
        }
        
        # Update index first (canonical)
        self._update_index(bead)
        
        # Write to session archive (full bead for rebuild)
        if session_id:
            bead_file = self.beads_dir / SESSION_FILE.format(id=session_id)
        else:
            bead_file = self.beads_dir / "global.jsonl"
        
        with open(bead_file, 'a') as f:
            f.write(json.dumps(bead) + "\n")
        
        # Append audit event (minimal - just id + timestamp for rebuild)
        events.event_bead_created(self.root, session_id, bead_id, now)
        
        return bead_id
    
    def capture_turn(
        self,
        role: str,
        content: str,
        tools_used: Optional[list] = None,
        user_message: str = "",
        session_id: str = "default"
    ):
        """
        Capture a single turn in the session.
        
        Args:
            role: assistant | user | system
            content: The message/response content
            tools_used: List of tools called
            user_message: The user input (for context)
            session_id: Session identifier
        """
        turn = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tools_used": tools_used or []
        }
        
        # Add user message context if provided
        if user_message:
            turn["user_message"] = user_message
        
        # Write to turns directory (separate from beads)
        turn_file = self.turns_dir / SESSION_FILE.format(id=session_id)
        
        with open(turn_file, 'a') as f:
            f.write(json.dumps(turn) + "\n")
    
    def consolidate(self, session_id: str = "default") -> dict:
        """
        Run session-end consolidation:
        - Summarize session to session_end bead
        - Update rolling window
        - Compact old beads
        
        Args:
            session_id: Session to consolidate
            
        Returns:
            Consolidation summary
        """
        # Read turns for this session
        turn_file = self.turns_dir / SESSION_FILE.format(id=session_id)
        
        if turn_file.exists():
            with open(turn_file, 'r') as f:
                turns = [json.loads(line) for line in f if line.strip()]
            turn_count = len(turns)
        else:
            turn_count = 0
        
        # Read beads for this session
        bead_file = self.beads_dir / SESSION_FILE.format(id=session_id)
        
        if bead_file.exists():
            with open(bead_file, 'r') as f:
                beads = [json.loads(line) for line in f if line.strip()]
            bead_count = len(beads)
        else:
            bead_count = 0
        
        # Create session_end bead
        end_bead_id = self.add_bead(
            type="session_end",
            title=f"Session {session_id} summary",
            summary=[
                f"{turn_count} turns",
                f"{bead_count} events"
            ],
            detail=f"Session {session_id} completed.",
            session_id=session_id,
            scope="project",
            tags=["session", session_id]
        )
        
        return {
            "session_id": session_id,
            "turns": turn_count,
            "events": bead_count,
            "end_bead": end_bead_id
        }
    
    def _normalize_enum(self, value, enum_class):
        """Normalize enum or string to string value."""
        if value is None:
            return None
        if isinstance(value, enum_class):
            return value.value
        return str(value)
    
    def query(
        self,
        type: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[list] = None,
        scope: Optional[str] = None,
        limit: int = 20
    ) -> list:
        """
        Query beads with filters.
        
        Args:
            type: Filter by bead type (BeadType enum or string)
            status: Filter by status (Status enum or string)
            tags: Filter by tags
            scope: Filter by scope (Scope enum or string)
            limit: Max results
            
        Returns:
            List of matching beads
        """
        from .models import BeadType, Status, Scope
        
        # Normalize enums to strings
        type_filter = self._normalize_enum(type, BeadType)
        status_filter = self._normalize_enum(status, Status)
        scope_filter = self._normalize_enum(scope, Scope)
        
        index = self._read_json(self.beads_dir / INDEX_FILE)
        results = []
        
        for bead_id, bead in index.get("beads", {}).items():
            if type_filter and bead.get("type") != type_filter:
                continue
            if status_filter and bead.get("status") != status_filter:
                continue
            if scope_filter and bead.get("scope") != scope_filter:
                continue
            if tags:
                bead_tags = set(bead.get("tags", []))
                if not bead_tags.intersection(set(tags)):
                    continue
            results.append(bead)
            
            if len(results) >= limit:
                break
        
        return results
    
    def promote(self, bead_id: str) -> bool:
        """
        Promote a bead to long-term memory.
        
        Args:
            bead_id: ID of bead to promote
            
        Returns:
            Success
        """
        index = self._read_json(self.beads_dir / INDEX_FILE)
        
        if bead_id not in index["beads"]:
            return False
        
        bead = index["beads"][bead_id]
        bead["status"] = "promoted"
        bead["promoted_at"] = datetime.now(timezone.utc).isoformat()
        
        index["beads"][bead_id] = bead
        self._write_json(self.beads_dir / INDEX_FILE, index)
        
        # Append audit event (rebuild support)

        events.event_bead_promoted(self.root, bead_id)
        
        return True
    
    def link(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        explanation: str = ""
    ) -> str:
        """
        Create a link between two beads.
        
        Args:
            source_id: Source bead ID
            target_id: Target bead ID
            relationship: Link type (caused_by, led_to, contradicts, etc.)
            explanation: Why they're linked
            
        Returns:
            Association ID
        """
        assoc_id = f"assoc-{uuid.uuid4().hex[:12].upper()}"
        
        assoc = {
            "id": assoc_id,
            "type": "association",
            "source_bead": source_id,
            "target_bead": target_id,
            "relationship": relationship,
            "explanation": explanation,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        index = self._read_json(self.beads_dir / INDEX_FILE)
        index["associations"].append(assoc)
        index["stats"]["total_associations"] += 1
        self._write_json(self.beads_dir / INDEX_FILE, index)
        
        # Append audit event (rebuild support)

        events.event_association_created(self.root, assoc)
        
        return assoc_id
    
    def recall(self, bead_id: str) -> bool:
        """
        Record a recall (strengthens association, myelination).
        
        Args:
            bead_id: ID of bead being recalled
            
        Returns:
            Success
        """
        index = self._read_json(self.beads_dir / INDEX_FILE)
        
        if bead_id not in index["beads"]:
            return False
        
        bead = index["beads"][bead_id]
        bead["recall_count"] = bead.get("recall_count", 0) + 1
        bead["last_recalled"] = datetime.now(timezone.utc).isoformat()
        
        index["beads"][bead_id] = bead
        self._write_json(self.beads_dir / INDEX_FILE, index)
        
        # Append audit event (rebuild support)

        events.event_bead_recalled(self.root, bead_id)
        
        return True
    
    def dream(self) -> list:
        """
        Run Dreamer association analysis.
        
        Returns:
            List of discovered associations
        """
        try:
            from . import dreamer
            # Pass the store instance for decoupled access
            return dreamer.run_analysis(store=self)
        except ImportError:
            return [{"error": "Dreamer not available"}]
    
    def rebuild_index(self) -> dict:
        """
        Rebuild the index from all events.
        
        This is the canonical way to ensure index consistency.
        Call this if you suspect index corruption.
        
        Returns:
            The rebuilt index
        """

        return events.rebuild_index(self.root)
    
    def stats(self) -> dict:
        """Get memory statistics."""
        index = self._read_json(self.beads_dir / INDEX_FILE)
        
        by_type = {}
        by_status = {}
        for bead in index.get("beads", {}).values():
            t = bead.get("type", "unknown")
            s = bead.get("status", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
            by_status[s] = by_status.get(s, 0) + 1
        
        return {
            "total_beads": len(index.get("beads", {})),
            "total_associations": len(index.get("associations", [])),
            "by_type": by_type,
            "by_status": by_status
        }
    
    # === Internal ===
    
    def _update_index(self, bead: dict):
        """Update the index with a new/updated bead."""
        index_file = self.beads_dir / INDEX_FILE
        index = self._read_json(index_file)
        
        index["beads"][bead["id"]] = bead
        index["stats"]["total_beads"] = len(index["beads"])
        
        self._write_json(index_file, index)
