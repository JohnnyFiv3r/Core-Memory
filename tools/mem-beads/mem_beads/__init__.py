#!/usr/bin/env python3
"""
mem-beads: Persistent causal agent memory with lossless compaction.

CLI for creating, querying, linking, compacting, and uncompacting beads.
Zero external dependencies — stdlib only.

Version: 1.0.0
"""

import argparse
import fcntl
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────

# PATCH 1: Unified storage root - MEMBEADS_ROOT
# Default to .mem-beads in workspace, derive all paths from this single root
# PATCH 10.A.1: Also support MEMBEADS_DIR for backward compatibility
_default_root = os.environ.get("MEMBEADS_DIR") or os.path.join(
    os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace")),
    ".mem-beads"
)
MEMBEADS_ROOT = os.environ.get("MEMBEADS_ROOT", _default_root)

# For backward compatibility, keep BEADS_DIR pointing to MEMBEADS_ROOT
BEADS_DIR = MEMBEADS_ROOT

# Derived paths - all subdirectories/files under one root
SESSIONS_DIR = os.path.join(MEMBEADS_ROOT, "sessions")
INDEX_FILE = "index.json"
EDGES_FILE = "edges.jsonl"
GLOBAL_FILE = "global.jsonl"

CROCKFORD_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

BEAD_TYPES = {
    "session_start", "session_end",
    "goal", "decision", "tool_call", "evidence",
    "outcome", "lesson", "checkpoint", "precedent",
    "context", "association",
    "promoted_lesson", "promoted_decision",
    # New types for richer graph
    "failed_hypothesis", "reversal", "misjudgment", 
    "overfitted_pattern", "abandoned_path",
    "reflection", "design_principle",
}

LINK_TYPES = {
    # Causal links (must be acyclic)
    "follows",          # Adjacent step in causal chain
    "derives-from",     # Built on or extends earlier bead
    "supersedes",      # Source replaces target as current truth
    "extends",         # Extends prior decision/lesson
    
    # Response links (may cycle)
    "responds-to",     # Response to specific prior bead
    "continues",       # Continues same work stream
    
    # Validation links (may cycle except revises)
    "validates",       # Confirms or proves earlier hypothesis
    "revises",         # Corrects or contradicts earlier (acyclic)
    
    # Context links (may cycle)
    "context",         # Background context for source bead
    "related",         # Loose association (bidirectional)
    "recalls",        # References prior session memory
}

# Links that must remain acyclic (cycle detection required)
ACYCLIC_LINK_TYPES = {"follows", "derives-from", "extends", "supersedes", "revises"}

# Links that may cycle freely
CYCLIC_LINK_TYPES = {"responds-to", "continues", "related", "context", "recalls", "validates"}

STATUSES = {"open", "closed", "promoted", "compacted", "superseded", "tombstoned"}

PROMOTION_ELIGIBLE = BEAD_TYPES - {"session_start", "session_end", "checkpoint", "association"}

# ── Lifecycle State Machine ─────────────────────────────────────────────

# Lifecycle states and their allowed transitions
LIFECYCLE_STATES = {
    "open": {"closed", "promoted", "compacted"},
    "closed": {"promoted", "compacted", "tombstoned"},
    "promoted": {"superseded", "compacted"},  # Can be superseded by newer truth
    "compacted": {"tombstoned", "promoted"},  # Can be promoted back or tombstoned
    "superseded": {"tombstoned"},
    "tombstoned": set(),  # Terminal state - no transitions
}

# Tier mapping (for token budget)
STATUS_TO_TIER = {
    "open": "full",
    "closed": "full", 
    "promoted": "full",
    "compacted": "summary",
    "superseded": "minimal",
    "tombstoned": "tombstoned",
}

# Tombstone behavior rules
# Tombstoned beads are:
# - NOT injectable in context packets
# - Still traversable for audit/chain queries
# - Still valid in supersede chains
TOMBSTONE_RULES = {
    "injectable": False,
    "traversable": True,
    "edges_valid": True,
    "estimated_tokens": 5,
}

def is_injectable(bead_id: str) -> bool:
    """Check if a bead is injectable in context packets."""
    index = load_index()
    bead = index.get("beads", {}).get(bead_id)
    if not bead:
        return False
    return bead.get("status") != "tombstoned"

# Valid state transitions
def can_transition(from_status: str, to_status: str) -> bool:
    """Check if a status transition is valid."""
    return to_status in LIFECYCLE_STATES.get(from_status, set())


def transition_bead(bead_id: str, new_status: str, reason: str | None = None) -> dict:
    """Transition a bead to a new status.
    
    Args:
        bead_id: The bead to transition
        new_status: Target status
        reason: Optional reason for the transition
    
    Returns:
        The updated bead
    
    Raises:
        ValueError: If transition is not allowed
    """
    index = load_index()
    bead = index.get("beads", {}).get(bead_id)
    if not bead:
        raise ValueError(f"Bead not found: {bead_id}")
    
    current_status = bead.get("status", "open")
    
    if not can_transition(current_status, new_status):
        raise ValueError(f"Invalid transition: {current_status} -> {new_status}")
    
    # Update status
    bead["status"] = new_status
    bead["status_changed_at"] = datetime.utcnow().isoformat() + "Z"
    if reason:
        bead["status_reason"] = reason
    
    # Special handling for certain transitions
    if new_status == "superseded":
        # Link to the bead that superseded this one
        pass
    
    save_index(index)
    return bead


# ── ULID Generation (stdlib) ──────────────────────────────────────────

def _encode_crockford(value: int, length: int) -> str:
    result = []
    for _ in range(length):
        result.append(CROCKFORD_B32[value & 31])
        value >>= 5
    return "".join(reversed(result))

def generate_ulid() -> str:
    ts_ms = int(time.time() * 1000)
    ts_part = _encode_crockford(ts_ms, 10)
    rand_part = "".join(random.choices(CROCKFORD_B32, k=16))
    return ts_part + rand_part

# ── File Locking ──────────────────────────────────────────────────────

class FileLock:
    """Simple advisory file lock using fcntl."""
    def __init__(self, path: str):
        self.path = path + ".lock"
        self.fd = None

    def __enter__(self):
        self.fd = open(self.path, "w")
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args):
        if self.fd:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.fd.close()

# ── Index Management ──────────────────────────────────────────────────

def _index_path() -> str:
    return os.path.join(MEMBEADS_ROOT, INDEX_FILE)


def ensure_dirs():
    """Ensure MEMBEADS_ROOT and sessions/ directories exist."""
    os.makedirs(MEMBEADS_ROOT, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)

def load_index() -> dict:
    path = _index_path()
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {"beads": {}, "sessions": {}, "version": 1}

def save_index(index: dict):
    """Save index atomically: write to .tmp, then rename."""
    path = _index_path()
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(index, f, indent=2)
    os.replace(tmp_path, path)  # Atomic on POSIX

def index_bead(index: dict, bead: dict, jsonl_file: str, line_num: int):
    """Add a bead to the in-memory index."""
    bead_id = bead["id"]
    index["beads"][bead_id] = {
        "type": bead["type"],
        "session_id": bead.get("session_id"),
        "status": bead.get("status", "open"),
        "title": bead.get("title", ""),
        "file": jsonl_file,
        "line": line_num,
        "created_at": bead["created_at"],
        "tags": bead.get("tags", []),
        "scope": bead.get("scope"),
        "recall_count": 0,
        "last_recalled": None,
    }
    # Track sessions with full metadata
    sid = bead.get("session_id")
    if sid:
        if sid not in index["sessions"]:
            index["sessions"][sid] = {
                "file": jsonl_file,
                "bead_count": 0,
                "bead_ids": [],
                "started_at": bead["created_at"],
                "ended_at": bead["created_at"],
                "estimated_token_footprint": 0,
            }
        index["sessions"][sid]["bead_count"] += 1
        index["sessions"][sid]["bead_ids"].append(bead_id)
        # Update ended_at to latest bead and accumulate tokens
        if bead["created_at"] > index["sessions"][sid]["ended_at"]:
            index["sessions"][sid]["ended_at"] = bead["created_at"]
        index["sessions"][sid]["estimated_token_footprint"] += estimate_tokens(bead)

# ── JSONL I/O ─────────────────────────────────────────────────────────

def _session_file(session_id: str) -> str:
    """Get path to session file. Uses sessions/ subdirectory if it exists."""
    # Try sessions/ subdirectory first
    session_path = os.path.join(SESSIONS_DIR, f"session-{session_id}.jsonl")
    if os.path.exists(os.path.dirname(session_path)):
        return session_path
    # Fall back to root
    return os.path.join(MEMBEADS_ROOT, f"session-{session_id}.jsonl")

def _global_file() -> str:
    return os.path.join(MEMBEADS_ROOT, GLOBAL_FILE)

def append_bead(bead: dict) -> str:
    """Append a bead to the appropriate JSONL file. Returns the file path."""
    os.makedirs(BEADS_DIR, exist_ok=True)
    session_id = bead.get("session_id")
    filepath = _session_file(session_id) if session_id else _global_file()

    with FileLock(filepath):
        # Count existing lines
        line_num = 0
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                line_num = sum(1 for _ in f)

        with open(filepath, "a") as f:
            f.write(json.dumps(bead, separators=(",", ":")) + "\n")

        # Update index
        index = load_index()
        index_bead(index, bead, os.path.basename(filepath), line_num)
        save_index(index)

    return filepath

# Token estimation: tier-aware for rolling window accuracy
def estimate_tokens(bead: dict, tier: str | None = None) -> int:
    """Estimate token count for a bead.
    
    Args:
        bead: The bead to estimate
        tier: Optional tier (full, summary, minimal, tombstoned).
              If not provided, infers from status.
    """
    if tier is None:
        status = bead.get("status", "open")
        tier = STATUS_TO_TIER.get(status, "full")
    
    # Use tier-specific budgets
    if tier == "tombstoned":
        return 5
    elif tier == "minimal":
        return 20
    elif tier == "summary":
        return 100
    else:
        content = json.dumps(bead, separators=(",", ":"))
        return max(len(content) // 4, 300)

# ── Edge Store (First-Class Links) ───────────────────────────────────

# TODO: Abstract behind EdgeStore interface for graph DB swap (V2)
# class EdgeStore:
#     def add(self, source_id, target_id, link_type, **kwargs) -> dict
#     def remove(self, edge_id: str) -> bool
#     def neighbors(self, bead_id: str) -> dict  # {incoming: [], outgoing: []}
#     def validate(self) -> dict

# PATCH 8: Derived-edge stats schema for myelination
EDGE_CLASSES = ("authored", "derived")
EDGE_TIERS = ("fresh", "stable", "weak")  # For myelination

# Edge stats stored on derived edges only (authored edges are immutable truth)
EDGE_STATS_DEFAULTS = {
    "retrieval_count": 0,
    "last_retrieved_at": None,
    "strength": 1.0,  # 0.0-1.0, decays over time
    "tier": "fresh",  # fresh|stable|weak
}


def _edges_path() -> str:
    """Get path to edges store."""
    return os.path.join(MEMBEADS_ROOT, EDGES_FILE)

def add_edge(source_id: str, target_id: str, link_type: str, scope: str = "session", thread_id: str | None = None, metadata: dict | None = None, edge_class: str = "authored") -> dict:
    """Add an edge to the edge store. Returns the created edge.
    
    Args:
        source_id: The source bead ID (current/newer bead)
        target_id: The target bead ID (linked/older bead)
        link_type: Type of link (follows, derives-from, etc.)
        scope: session or cross-session
        thread_id: Optional thread for grouping
        metadata: Optional metadata dict
        edge_class: "authored" (model-written) or "derived" (crawler/association)
    """
    ensure_dirs()  # PATCH 10.C.9
    if link_type not in LINK_TYPES:
        raise ValueError(f"Unknown link type: {link_type}. Valid: {sorted(LINK_TYPES)}")
    
    if edge_class not in ("authored", "derived"):
        raise ValueError(f"Invalid edge_class: {edge_class}. Valid: authored, derived")
    
    # Check for cycles on protected types
    if link_type in ACYCLIC_LINK_TYPES:
        if would_create_cycle(source_id, target_id, link_type):
            raise ValueError(f"Adding edge ({source_id} -> {target_id}, {link_type}) would create a cycle")
    
    edge = {
        "id": f"edge-{generate_ulid()}",
        "source_id": source_id,
        "target_id": target_id,
        "type": link_type,
        "scope": scope,
        "thread_id": thread_id,
        "edge_class": edge_class,
        "metadata": metadata or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    
    # PATCH 7 & 8: Add edge stats for derived edges (for myelination)
    if edge_class == "derived":
        edge["stats"] = EDGE_STATS_DEFAULTS.copy()
    
    # Ensure edges file exists
    edges_path = _edges_path()
    os.makedirs(os.path.dirname(edges_path), exist_ok=True)
    
    # Check for existing edge (source_id, target_id, type) - update if exists
    existing = find_edge(source_id, target_id, link_type)
    if existing:
        existing["metadata"] = metadata or {}
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        edge = existing
        # Rewrite edges file with update
        rewrite_edges_file(existing)
    else:
        # PATCH 10.B.7: Append new edge with lock
        with FileLock(edges_path):
            with open(edges_path, "a") as f:
                f.write(json.dumps(edge, separators=(",", ":")) + "\n")
    
    return edge

def find_edge(source_id: str, target_id: str, link_type: str) -> dict | None:
    """Find an edge by source, target, and type."""
    edges_path = _edges_path()
    if not os.path.exists(edges_path):
        return None
    
    with open(edges_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                edge = json.loads(line)
                if edge.get("source_id") == source_id and edge.get("target_id") == target_id and edge.get("type") == link_type:
                    return edge
            except json.JSONDecodeError:
                continue
    return None

def get_edges_from(bead_id: str) -> list[dict]:
    """Get all edges where bead is the source."""
    edges_path = _edges_path()
    if not os.path.exists(edges_path):
        return []
    
    edges = []
    with open(edges_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                edge = json.loads(line)
                if edge.get("source_id") == bead_id:
                    edges.append(edge)
            except json.JSONDecodeError:
                continue
    return edges

def get_edges_to(bead_id: str) -> list[dict]:
    """Get all edges where bead is the target (reverse lookup)."""
    edges_path = _edges_path()
    if not os.path.exists(edges_path):
        return []
    
    edges = []
    with open(edges_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                edge = json.loads(line)
                if edge.get("target_id") == bead_id:
                    edges.append(edge)
            except json.JSONDecodeError:
                continue
    return edges

def get_neighbors(bead_id: str) -> dict:
    """Get all neighbors (both directions) for a bead."""
    return {
        "from": get_edges_from(bead_id),
        "to": get_edges_to(bead_id),
    }

# ── Chain Queries ─────────────────────────────────────────────────────

# Default traversal sets for chain queries
# Up chain: what led TO this bead (causes)
# Edge: source=current bead, target=linked bead
# "A follows B" means A caused by B, so follow edges FROM current (source)
UP_CHAIN_TYPES = {"derives-from", "extends", "follows"}
# Down chain: what resulted FROM this bead (effects)
# Follow edges TO current (where current is target)
DOWN_CHAIN_TYPES = {"validates", "supersedes", "revises", "follows", "derives-from", "extends"}
CONTEXT_TYPES = {"context", "related", "recalls"}
RESPONSE_TYPES = {"responds-to", "continues"}

# ── Semantic Traversal Rules ─────────────────────────────────────────────
# Formalized traversal semantics for context packet assembly

# WHY types: traversal finds causal ancestry (why did this happen?)
# Used for: explaining decisions, root cause analysis
WHY_TYPES = {"derives-from", "extends", "follows"}

# CHANGE types: traversal finds truth transitions (what superseded this?)
# Used for: understanding evolution of thinking, finding current truth
CHANGE_TYPES = {"supersedes", "revises"}

# EFFECT types: traversal finds downstream consequences
# Used for: impact analysis, understanding ripple effects
EFFECT_TYPES = {"validates", "supersedes", "revises", "follows", "derives-from", "extends"}

# Neutral/background: context without causal weight
BACKGROUND_TYPES = {"context", "related", "recalls"}


def get_up_chain(bead_id: str, max_depth: int = 10, include_derived: bool = False) -> list[dict]:
    """Get beads that led TO this bead (causal roots).
    
    Edge direction: source = newer bead, target = older bead
    "A follows B" means A (newer) was caused by B (older).
    Edge: source=A (effect), target=B (cause)
    
    To find causes: look at edges FROM this bead, the targets are causes.
    
    Args:
        bead_id: Starting bead
        max_depth: Maximum traversal depth
        include_derived: If False (default), exclude derived edges for baseline context.
    Returns: list of beads, ordered by distance (farthest/oldest first)
    """
    visited = set()
    queue = [(bead_id, 0)]
    chain = []
    
    while queue:
        current, depth = queue.pop(0)
        if current in visited or depth > max_depth:
            continue
        visited.add(current)
        
        # Get edges FROM current (current is source = effect, target is cause)
        for edge in get_edges_from(current):
            if not include_derived and edge.get("edge_class") == "derived":
                continue
            
            if edge.get("type") in UP_CHAIN_TYPES:
                cause = edge.get("target_id")  # Target is the cause
                next_depth = depth + 1
                if next_depth <= max_depth:
                    chain.append({"bead_id": cause, "edge": edge, "distance": next_depth})
                if next_depth < max_depth:
                    queue.append((cause, next_depth))
    
    # Sort by distance (farthest first)
    chain.sort(key=lambda x: x["distance"], reverse=True)
    return chain

def get_down_chain(bead_id: str, max_depth: int = 10, include_derived: bool = False) -> list[dict]:
    """Get beads that resulted FROM this bead (effects).
    
    Edge direction: source = newer bead, target = older bead
    "A follows B" means A (newer) was caused by B (older).
    Edge: source=A (effect), target=B (cause)
    
    To find effects: look at edges TO this bead, the sources are effects.
    
    Args:
        bead_id: Starting bead
        max_depth: Maximum traversal depth
        include_derived: If False (default), exclude derived edges.
    Returns: list of beads, ordered by distance
    """
    visited = set()
    queue = [(bead_id, 0)]
    chain = []
    
    while queue:
        current, depth = queue.pop(0)
        if current in visited or depth > max_depth:
            continue
        visited.add(current)
        
        # Get edges TO current (current is target = cause, source is effect)
        for edge in get_edges_to(current):
            if not include_derived and edge.get("edge_class") == "derived":
                continue
            
            if edge.get("type") in DOWN_CHAIN_TYPES:
                effect = edge.get("source_id")  # Source is the effect
                next_depth = depth + 1
                if next_depth <= max_depth:
                    chain.append({"bead_id": effect, "edge": edge, "distance": next_depth})
                if next_depth < max_depth:
                    queue.append((effect, next_depth))
    
    # Sort by distance
    chain.sort(key=lambda x: x["distance"])
    return chain


def get_context_chain(bead_id: str) -> list[dict]:
    """Get contextual neighbors (context, related, recalls links)."""
    context = []
    
    for edge in get_edges_from(bead_id):
        if edge.get("type") in CONTEXT_TYPES:
            context.append({"bead_id": edge.get("target_id"), "edge": edge})
    
    for edge in get_edges_to(bead_id):
        if edge.get("type") in CONTEXT_TYPES:
            context.append({"bead_id": edge.get("source_id"), "edge": edge})
    
    return context

def get_all_edges() -> list[dict]:
    """Get all edges from the store."""
    edges_path = _edges_path()
    if not os.path.exists(edges_path):
        return []
    
    edges = []
    with open(edges_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                edges.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return edges


# PATCH 7: Edge-usage instrumentation hook (for myelination)
def record_edge_used(edge_id: str) -> dict | None:
    """Record that an edge was used during traversal.
    
    This is called when chain traversal includes an edge to include a bead
    in a context packet. Only derived edges accumulate usage stats;
    authored edges are immutable causal truth.
    
    Args:
        edge_id: The edge that was traversed
        
    Returns:
        Updated edge dict, or None if edge not found/not derived
    """
    edges_path = _edges_path()
    if not os.path.exists(edges_path):
        return None
    
    # Find and update the edge
    edges = []
    updated_edge = None
    
    with open(edges_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                edge = json.loads(line)
                if edge.get("id") == edge_id:
                    # Only update derived edges
                    if edge.get("edge_class") != "derived":
                        return None
                    
                    # Initialize stats if missing
                    if "stats" not in edge:
                        edge["stats"] = EDGE_STATS_DEFAULTS.copy()
                    
                    # Increment retrieval count
                    edge["stats"]["retrieval_count"] = edge["stats"].get("retrieval_count", 0) + 1
                    edge["stats"]["last_retrieved_at"] = datetime.now(timezone.utc).isoformat()
                    
                    # Update tier based on retrieval count
                    count = edge["stats"]["retrieval_count"]
                    if count >= 10:
                        edge["stats"]["tier"] = "stable"
                        edge["stats"]["strength"] = min(1.0, edge["stats"].get("strength", 1.0) + 0.1)
                    elif count >= 3:
                        edge["stats"]["tier"] = "stable"
                    
                    updated_edge = edge
                edges.append(edge)
            except json.JSONDecodeError:
                continue
    
    if updated_edge:
        # Write back all edges
        with open(edges_path, "w") as f:
            for edge in edges:
                f.write(json.dumps(edge, separators=(",", ":")) + "\n")
        return updated_edge
    
    return None


# PATCH 7: Record edge usage when traversing chains
def _record_chain_edge_usage(chain_result: list[dict]):
    """Record usage for all edges in a chain traversal result.
    
    This is called by build_context_packet when including chains.
    """
    for item in chain_result:
        edge = item.get("edge", {})
        if edge.get("edge_class") == "derived":
            record_edge_used(edge.get("id"))


def validate_edges() -> dict:
    """Validate edge store integrity.
    
    PATCH 9: Expanded validation to check:
    - Dangling bead IDs (source or target not in index)
    - Invalid link types
    - Self loops
    - Missing or invalid edge_class
    - Invalid edge stats (for derived edges)
    - Invalid tier values
    
    Returns:
        Dict with validation results and issues found
    """
    index = load_index()
    bead_ids = set(index.get("beads", {}).keys())
    
    edges = get_all_edges()
    
    issues = {
        "dangling_sources": [],
        "dangling_targets": [],
        "invalid_types": [],
        "self_loops": [],
        "missing_class": [],
        "invalid_class": [],  # PATCH 9: invalid edge_class values
        "invalid_tier": [],   # PATCH 9: invalid tier values
        "invalid_stats": [],  # PATCH 9: invalid stats for derived edges
    }
    
    for edge in edges:
        source = edge.get("source_id")
        target = edge.get("target_id")
        link_type = edge.get("type")
        edge_class = edge.get("edge_class")
        
        # Check for self-loops
        if source == target:
            issues["self_loops"].append({
                "edge_id": edge.get("id"),
                "bead_id": source,
            })
        
        # Check dangling IDs
        if source and source not in bead_ids:
            issues["dangling_sources"].append({
                "edge_id": edge.get("id"),
                "bead_id": source,
            })
        
        if target and target not in bead_ids:
            issues["dangling_targets"].append({
                "edge_id": edge.get("id"),
                "bead_id": target,
            })
        
        # Check invalid types
        if link_type and link_type not in LINK_TYPES:
            issues["invalid_types"].append({
                "edge_id": edge.get("id"),
                "type": link_type,
            })
        
        # Check missing edge_class
        if not edge_class:
            issues["missing_class"].append({
                "edge_id": edge.get("id"),
            })
        
        # PATCH 9: Check invalid edge_class values
        if edge_class and edge_class not in EDGE_CLASSES:
            issues["invalid_class"].append({
                "edge_id": edge.get("id"),
                "edge_class": edge_class,
            })
        
        # PATCH 9: Check derived edge stats
        if edge_class == "derived":
            stats = edge.get("stats", {})
            if not stats:
                issues["invalid_stats"].append({
                    "edge_id": edge.get("id"),
                    "reason": "missing_stats",
                })
            else:
                # Check tier validity
                tier = stats.get("tier")
                if tier and tier not in EDGE_TIERS:
                    issues["invalid_tier"].append({
                        "edge_id": edge.get("id"),
                        "tier": tier,
                    })
                # Check strength range
                strength = stats.get("strength")
                if strength is not None and not (0.0 <= strength <= 1.0):
                    issues["invalid_stats"].append({
                        "edge_id": edge.get("id"),
                        "reason": "invalid_strength",
                        "strength": strength,
                    })
        
        # PATCH 9.1: Authored edges must NOT have stats (they're immutable causal truth)
        if edge_class == "authored" and edge.get("stats"):
            issues["invalid_stats"].append({
                "edge_id": edge.get("id"),
                "reason": "authored_edge_cannot_have_stats",
            })
    
    # Check for cycles in protected types
    cycles = []
    for edge in edges:
        if edge.get("type") in ACYCLIC_LINK_TYPES:
            try:
                if would_create_cycle(edge["source_id"], edge["target_id"], edge["type"]):
                    cycles.append({
                        "edge_id": edge.get("id"),
                        "source": edge["source_id"],
                        "target": edge["target_id"],
                        "type": edge["type"],
                    })
            except RecursionError:
                # Very deep chain, skip cycle check
                pass
    
    is_valid = (
        len(issues["dangling_sources"]) == 0 and
        len(issues["dangling_targets"]) == 0 and
        len(issues["invalid_types"]) == 0 and
        len(issues["self_loops"]) == 0 and
        len(issues["invalid_class"]) == 0 and
        len(issues["invalid_tier"]) == 0 and
        len(issues["invalid_stats"]) == 0 and
        len(cycles) == 0
    )
    
    return {
        "valid": is_valid,
        "total_edges": len(edges),
        "issues": {k: v for k, v in issues.items() if v},
        "cycles": cycles,
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }

# ── Context Packet Builder (Pure Function) ──────────────────────────────

def build_context_packet(
    session_ids: list[str] | None = None,
    bead_types: list[str] | None = None,
    statuses: list[str] | None = None,
    tags: list[str] | None = None,
    limit: int = 50,
    include_chains: bool = True,
    max_chain_depth: int = 3,
    use_rolling_window: bool = True,
    explain_superseded: bool = False,
) -> dict:
    """Build a context packet from beads (pure function - no mutations).
    
    PATCH 5: Now consumes tier_plan from rolling window
    PATCH 6: Implements supersedes semantics - injects newest truth only
    PATCH 7: Records edge usage when traversing chains
    
    This assembles context for injection without modifying the store.
    
    Args:
        session_ids: Filter to specific sessions (None = all)
        bead_types: Filter to specific bead types (None = all)
        statuses: Filter to specific statuses (None = all)
        tags: Filter to beads with ANY of these tags (None = all)
        limit: Maximum beads to include
        include_chains: Include causal chains for each bead
        max_chain_depth: Max depth for chain traversal
        use_rolling_window: Use rolling window for tier selection (PATCH 5)
        explain_superseded: If True, include superseded beads for debugging (PATCH 6)
    
    Returns:
        Context packet dict with beads, metadata, and stats
    """
    index = load_index()
    all_beads = []
    
    # PATCH 5 & 9.1 FIX: Get tier plan from rolling window if requested
    tier_plan = {}
    if use_rolling_window:
        window_result = compute_rolling_window()
        tier_plan = window_result.get("tier_plan", {})
        
        # PATCH 9.1 FIX: Default session_ids to computed window when not specified
        if session_ids is None:
            session_ids = window_result.get("sessions", [])
    
    # Build supersedes map: bead_id -> newest_bead_id
    supersedes_map = {}  # old_id -> new_id
    for bead_id, bead_meta in index.get("beads", {}).items():
        if bead_meta.get("status") == "superseded":
            superseded_by = bead_meta.get("superseded_by")
            if superseded_by:
                supersedes_map[bead_id] = superseded_by
    
    # PATCH 6: Track which bead IDs should be excluded (superseded)
    # PATCH 9.1 FIX: Only exclude OLD (superseded) beads, keep the NEW (superseding) bead
    excluded_beads = set()
    if not explain_superseded:
        # Only add the superseded (old) beads to exclusion set
        # The NEW bead that supersedes is NOT excluded - it's the current truth
        for old_id in supersedes_map.keys():
            excluded_beads.add(old_id)
    
    # Collect all beads from index
    for bead_id, bead_meta in index.get("beads", {}).items():
        # PATCH 6: Skip excluded superseded beads unless in explain mode
        if bead_id in excluded_beads and not explain_superseded:
            continue
        
        # Apply filters
        if session_ids and bead_meta.get("session_id") not in session_ids:
            continue
        if bead_types and bead_meta.get("type") not in bead_types:
            continue
        if statuses and bead_meta.get("status") not in statuses:
            continue
        if tags:
            bead_tags = set(bead_meta.get("tags", []))
            if not bead_tags.intersection(tags):
                continue
        
        # PATCH 6: Skip tombstoned beads (not injectable)
        if bead_meta.get("status") == "tombstoned":
            continue
        
        # Reconstruct bead from file
        filepath = os.path.join(MEMBEADS_ROOT, bead_meta.get("file", ""))
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                lines = f.readlines()
                line_num = bead_meta.get("line", 0)
                if line_num < len(lines):
                    try:
                        bead = json.loads(lines[line_num].strip())
                        # Add tier info from rolling window
                        bead["_tier"] = tier_plan.get(bead_id, "full")
                        all_beads.append(bead)
                    except json.JSONDecodeError:
                        continue
    
    # Sort by created_at (newest first)
    all_beads.sort(key=lambda b: b.get("created_at", ""), reverse=True)
    
    # PATCH 9.1 FIX: Make budget-driven, not limit-driven
    # Select beads using running token accumulator, stop when budget hit
    budget_config = get_token_budget_config()
    context_budget = budget_config["context_budget_tokens"]
    
    selected_beads = []
    total_tokens = 0
    
    for bead in all_beads:
        # Check budget BEFORE adding (tier-aware)
        tier = bead.get("_tier", "full")
        bead_tokens = estimate_tokens(bead, tier=tier)
        
        # If adding this bead would exceed budget, stop (unless it's the first bead)
        if total_tokens + bead_tokens > context_budget and selected_beads:
            # Try to make room by downgrading within selected beads
            # For now, stop adding more beads
            break
        
        selected_beads.append(bead)
        total_tokens += bead_tokens
    
    # Build result
    beads_data = []
    
    for bead in selected_beads:
        # Use tier from plan or default to full
        tier = bead.get("_tier", "full")
        bead_tokens = estimate_tokens(bead, tier=tier)
        total_tokens += bead_tokens
        
        bead_entry = {
            "bead": bead,
            "tier": tier,
            "estimated_tokens": bead_tokens,
        }
        
        # Include chains if requested
        if include_chains:
            up_chain = get_up_chain(bead["id"], max_depth=max_chain_depth)
            down_chain = get_down_chain(bead["id"], max_depth=max_chain_depth)
            context_chain = get_context_chain(bead["id"])
            
            # PATCH 7: Record edge usage for derived edges
            _record_chain_edge_usage(up_chain)
            _record_chain_edge_usage(down_chain)
            _record_chain_edge_usage(context_chain)
            
            bead_entry["up_chain"] = up_chain
            bead_entry["down_chain"] = down_chain
            bead_entry["context_chain"] = context_chain
        
        beads_data.append(bead_entry)
    
    # PATCH 5: Verify token budget is respected
    budget_config = get_token_budget_config()
    budget_respected = total_tokens <= budget_config["context_budget_tokens"]
    
    return {
        "version": 1,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "filters": {
            "session_ids": session_ids,
            "bead_types": bead_types,
            "statuses": statuses,
            "tags": tags,
            "limit": limit,
            "use_rolling_window": use_rolling_window,
            "explain_superseded": explain_superseded,
        },
        "stats": {
            "total_matched": len(all_beads),
            "beads_included": len(beads_data),
            "estimated_tokens": total_tokens,
            "budget_respected": budget_respected,
            "superseded_excluded": len(excluded_beads),
        },
        "beads": beads_data,
    }


# ── Token Budget Model ─────────────────────────────────────────────────

DEFAULT_CONTEXT_BUDGET_TOKENS = 10000  # Total context budget
DEFAULT_MAX_SESSION_TOKENS = 2500      # Cap per session
DEFAULT_MIN_SESSIONS_KEEP = 3          # Hard floor: always keep N sessions


def get_token_budget_config() -> dict:
    """Get token budget configuration from environment or defaults."""
    return {
        "context_budget_tokens": int(os.environ.get("MEMBEADS_CONTEXT_BUDGET", DEFAULT_CONTEXT_BUDGET_TOKENS)),
        "max_session_tokens": int(os.environ.get("MEMBEADS_MAX_SESSION_TOKENS", DEFAULT_MAX_SESSION_TOKENS)),
        "min_sessions_keep": int(os.environ.get("MEMBEADS_MIN_SESSIONS", DEFAULT_MIN_SESSIONS_KEEP)),
    }


def estimate_session_tokens(session_id: str) -> int:
    """Estimate total tokens for a session based on index."""
    index = load_index()
    session = index.get("sessions", {}).get(session_id, {})
    return session.get("estimated_token_footprint", 0)


def get_session_recency_score(session_id: str) -> float:
    """Get recency score for a session (0-1, newer = higher)."""
    index = load_index()
    session = index.get("sessions", {}).get(session_id, {})
    started = session.get("started_at", "")
    if not started:
        return 0.0
    
    try:
        # Simple recency: compare to now
        session_time = datetime.fromisoformat(started.replace("Z", "+00:00"))
        now = datetime.utcnow()
        age_hours = (now - session_time.replace(tzinfo=None)).total_seconds() / 3600
        # Decay: 1.0 at 0h, 0.5 at 24h, 0.1 at 168h (1 week)
        return max(0.0, 1.0 - (age_hours / 168.0))
    except (ValueError, OSError):
        return 0.5


# ── Rolling Window Algorithm ───────────────────────────────────────────

TIER_TOKEN_BUDGETS = {
    "full": 300,      # ~200-400 tokens
    "summary": 100,   # ~50-120 tokens
    "minimal": 20,    # ~10-25 tokens
    "tombstoned": 5,  # ~5 tokens (id only)
}


def get_bead_tier(bead_id: str) -> str:
    """Get the current tier of a bead based on its status."""
    index = load_index()
    bead = index.get("beads", {}).get(bead_id, {})
    status = bead.get("status", "open")
    
    # Map status to tier
    if status == "promoted":
        return "full"
    elif status == "compacted":
        return "summary"
    elif status == "tombstoned":
        return "tombstoned"
    else:
        return "full"  # Default to full for open beads


def compute_rolling_window(
    context_budget: int | None = None,
    max_session_tokens: int | None = None,
    min_sessions_keep: int | None = None,
) -> dict:
    """Compute rolling window selection using deterministic algorithm.
    
    PATCH 3: Returns explicit tier_plan (bead_id -> tier)
    PATCH 4: Enforces max_session_tokens per-session cap
    
    This is a PURE function - does not modify store.
    
    Algorithm:
    1. Sort sessions by recency (newest first)
    2. For each session:
       a. Check if session would exceed max_session_tokens
       b. If so, downgrade beads within that session first
       c. Then check if adding session would exceed context_budget
    3. Enforce min_sessions_keep hard floor
    4. Return tier_plan mapping each bead to its render tier
    
    Returns:
        Dict with:
        - sessions: selected session IDs
        - tier_plan: {bead_id: "full"|"summary"|"minimal"|"tombstoned"}
        - stats: token estimates
        - debug: dropped/downgraded info
    """
    config = get_token_budget_config()
    context_budget = context_budget or config["context_budget_tokens"]
    max_session_tokens = max_session_tokens or config["max_session_tokens"]
    min_sessions_keep = min_sessions_keep or config["min_sessions_keep"]
    
    index = load_index()
    sessions = index.get("sessions", {})
    
    # Sort sessions by recency (newest first)
    session_list = sorted(
        sessions.keys(),
        key=lambda sid: sessions[sid].get("started_at", ""),
        reverse=True
    )
    
    selected_sessions = []
    tier_plan = {}  # PATCH 3 & 9.1 FIX: Explicit tier plan {bead_id: tier}
    # PATCH 9.1 FIX: Only initialize for sessions we'll consider (don't pre-fill all)
    total_tokens = 0
    dropped = []
    downgraded = []
    
    # Phase 1: Select sessions by recency with max_session_tokens enforcement
    for sid in session_list:
        session_data = sessions[sid]
        session_bead_ids = session_data.get("bead_ids", [])
        
        # Calculate session tokens at current tier levels
        session_tokens = sum(
            TIER_TOKEN_BUDGETS.get(tier_plan.get(bid, "full"), 300)
            for bid in session_bead_ids
        )
        
        # PATCH 4 & 9.1 FIX: Enforce max_session_tokens - downgrade within session first
        # PATCH 9.1: Remove len(selected_sessions) >= 1 guard - apply to first session too
        if session_tokens > max_session_tokens:
            # Try to downgrade beads in this session to fit cap
            remaining = max_session_tokens
            session_beads_sorted = sorted(
                session_bead_ids,
                key=lambda bid: index.get("beads", {}).get(bid, {}).get("created_at", ""),
            )  # oldest first for downgrading
            
            for bid in session_beads_sorted:
                current_tier = tier_plan.get(bid, "full")
                if current_tier == "full":
                    tier_plan[bid] = "summary"
                    session_tokens -= (TIER_TOKEN_BUDGETS["full"] - TIER_TOKEN_BUDGETS["summary"])
                    downgraded.append({"bead_id": bid, "old_tier": "full", "new_tier": "summary", "session_id": sid})
                if session_tokens <= max_session_tokens:
                    break
            
            # If still over, go to minimal
            if session_tokens > max_session_tokens:
                for bid in session_beads_sorted:
                    current_tier = tier_plan.get(bid, "full")
                    if current_tier == "summary":
                        tier_plan[bid] = "minimal"
                        session_tokens -= (TIER_TOKEN_BUDGETS["summary"] - TIER_TOKEN_BUDGETS["minimal"])
                        downgraded.append({"bead_id": bid, "old_tier": "summary", "new_tier": "minimal", "session_id": sid})
                    if session_tokens <= max_session_tokens:
                        break
        
        # Check if adding this session would exceed context budget
        would_exceed = (total_tokens + session_tokens > context_budget and 
                        len(selected_sessions) >= min_sessions_keep)
        
        if would_exceed:
            # PATCH 9.1 FIX: Use needed_savings, not remaining_budget
            # needed_savings = how much we need to save to fit the NEW session
            needed_savings = (total_tokens + session_tokens) - context_budget
            
            # PATCH 9.1 FIX: Add candidate session beads to tier_plan for downgrading
            for bid in session_bead_ids:
                if bid not in tier_plan:
                    tier_plan[bid] = "full"
            
            # Find oldest non-pinned, non-promoted beads to downgrade
            beads_to_downgrade = _find_beads_for_downgrade(tier_plan, needed_savings)
            
            for bid, old_tier, new_tier in beads_to_downgrade:
                tier_plan[bid] = new_tier
                downgraded.append({
                    "bead_id": bid,
                    "old_tier": old_tier,
                    "new_tier": new_tier,
                })
                # Adjust token estimate
                old_tokens = TIER_TOKEN_BUDGETS.get(old_tier, 300)
                new_tokens = TIER_TOKEN_BUDGETS.get(new_tier, 100)
                total_tokens -= (old_tokens - new_tokens)
        
        # Recalculate session tokens after downgrades
        session_tokens = sum(
            TIER_TOKEN_BUDGETS.get(tier_plan.get(bid, "full"), 300)
            for bid in session_bead_ids
        )
        
        # Add session if within budget or enforcing min_sessions
        if total_tokens + session_tokens <= context_budget or len(selected_sessions) < min_sessions_keep:
            selected_sessions.append(sid)
            # PATCH 9.1 FIX: Add this session's beads to tier_plan
            for bid in session_bead_ids:
                if bid not in tier_plan:
                    tier_plan[bid] = "full"
            total_tokens += session_tokens
        else:
            dropped.append({"session_id": sid, "reason": "budget_exceeded"})
    
    return {
        "version": 1,
        "computed_at": datetime.utcnow().isoformat() + "Z",
        "config": {
            "context_budget_tokens": context_budget,
            "max_session_tokens": max_session_tokens,
            "min_sessions_keep": min_sessions_keep,
        },
        "stats": {
            "sessions_selected": len(selected_sessions),
            "beads_selected": len(tier_plan),
            "estimated_tokens": total_tokens,
        },
        "sessions": selected_sessions,
        "tier_plan": tier_plan,  # PATCH 3: Explicit tier plan
        "debug": {
            "dropped_sessions": dropped,
            "downgraded_beads": downgraded,
        },
    }


def _find_beads_for_downgrade(tier_plan: dict[str, str], remaining_budget: int) -> list[tuple]:
    """Find beads that can be downgraded to fit budget.
    
    PATCH 3: Now accepts tier_plan dict instead of bead list.
    
    Returns list of (bead_id, old_tier, new_tier) tuples.
    """
    index = load_index()
    downgrade_candidates = []
    
    for bead_id, current_tier in tier_plan.items():
        # Skip already downgraded beads
        if current_tier in ("minimal", "tombstoned"):
            continue
            
        bead = index.get("beads", {}).get(bead_id, {})
        
        # Don't downgrade pinned or promoted beads
        if bead.get("pinned") or bead.get("status") == "promoted":
            continue
        
        # Determine possible downgrade path
        if current_tier == "full":
            new_tier = "summary"
        elif current_tier == "summary":
            new_tier = "minimal"
        else:
            continue
        
        old_tokens = TIER_TOKEN_BUDGETS.get(current_tier, 300)
        new_tokens = TIER_TOKEN_BUDGETS.get(new_tier, 100)
        savings = old_tokens - new_tokens
        
        downgrade_candidates.append((bead_id, current_tier, new_tier, savings))
    
    # Sort by savings (most savings first)
    downgrade_candidates.sort(key=lambda x: x[3], reverse=True)
    
    # PATCH 3: Allow downgrading multiple beads until budget satisfied
    result = []
    accumulated_savings = 0
    for bead_id, old_tier, new_tier, savings in downgrade_candidates:
        if accumulated_savings >= remaining_budget:
            break
        result.append((bead_id, old_tier, new_tier))
        accumulated_savings += savings
    
    return result


# ── Lifecycle Triggers ───────────────────────────────────────────────────

def on_session_close(session_id: str) -> dict:
    """Trigger compaction pass when a session closes.
    
    This is the appropriate time to compact beads from the session
    since no new beads will be added.
    
    Args:
        session_id: The session that closed
    
    Returns:
        Summary of compaction actions taken
    """
    index = load_index()
    session = index.get("sessions", {}).get(session_id, {})
    bead_ids = session.get("bead_ids", [])
    
    compacted = []
    promoted = []
    
    for bead_id in bead_ids:
        bead = index.get("beads", {}).get(bead_id, {})
        current_status = bead.get("status", "open")
        
        # Skip if already promoted or pinned
        if bead.get("pinned") or current_status == "promoted":
            continue
        
        # Compact open beads to summary
        if current_status == "open":
            try:
                transition_bead(bead_id, "compacted", "session_close")
                compacted.append(bead_id)
            except ValueError:
                pass  # Transition not allowed
    
    return {
        "session_id": session_id,
        "compacted": compacted,
        "promoted": promoted,
        "action": "on_session_close",
    }


def on_budget_pressure() -> dict:
    """Trigger compaction when context budget is exceeded.
    
    This is an offline maintenance operation that downgrades
    beads to fit within budget constraints.
    
    Returns:
        Summary of downgrades performed
    """
    config = get_token_budget_config()
    budget = config["context_budget_tokens"]
    
    # Get current rolling window
    window = compute_rolling_window(context_budget=budget)
    
    # Check if we're over budget
    if window["stats"]["estimated_tokens"] <= budget:
        return {"action": "on_budget_pressure", "status": "ok", "downgraded": []}
    
    # Perform downgrades
    downgraded = []
    for item in window.get("debug", {}).get("downgraded_beads", []):
        bead_id = item["bead_id"]
        new_tier = item["new_tier"]
        
        # Map tier to status
        tier_to_status = {"full": "open", "summary": "compacted", "minimal": "superseded", "tombstoned": "tombstoned"}
        
        try:
            new_status = tier_to_status.get(new_tier, "compacted")
            transition_bead(bead_id, new_status, "budget_pressure")
            downgraded.append({"bead_id": bead_id, "new_status": new_status})
        except ValueError:
            pass
    
    return {
        "action": "on_budget_pressure",
        "status": "completed",
        "downgraded": downgraded,
    }


def get_bead_status(bead_id: str) -> str | None:
    """Get the current status of a bead."""
    index = load_index()
    bead = index.get("beads", {}).get(bead_id)
    return bead.get("status") if bead else None


def is_pinned(bead_id: str) -> bool:
    """Check if a bead is pinned (exempt from compaction)."""
    index = load_index()
    bead = index.get("beads", {}).get(bead_id, {})
    return bead.get("pinned", False)


def pin_bead(bead_id: str) -> dict:
    """Pin a bead to prevent compaction."""
    index = load_index()
    bead = index.get("beads", {}).get(bead_id)
    if not bead:
        raise ValueError(f"Bead not found: {bead_id}")
    
    bead["pinned"] = True
    bead["pinned_at"] = datetime.utcnow().isoformat() + "Z"
    save_index(index)
    return bead


def unpin_bead(bead_id: str) -> dict:
    """Unpin a bead to allow compaction."""
    index = load_index()
    bead = index.get("beads", {}).get(bead_id)
    if not bead:
        raise ValueError(f"Bead not found: {bead_id}")
    
    bead["pinned"] = False
    bead["unpinned_at"] = datetime.utcnow().isoformat() + "Z"
    save_index(index)
    return bead


def would_create_cycle(source_id: str, target_id: str, link_type: str) -> bool:
    """Check if adding an edge would create a cycle (for acyclic link types).
    
    Only considers cycles within the same link type.
    """
    # DFS from target_id to see if we can reach source_id via same link type
    visited = set()
    stack = [target_id]
    
    while stack:
        current = stack.pop()
        if current == source_id:
            return True  # Cycle detected
        if current in visited:
            continue
        visited.add(current)
        
        # Follow edges from current, only considering the same link type
        for edge in get_edges_from(current):
            if edge.get("type") == link_type:  # Same type only
                stack.append(edge.get("target_id"))
    
    return False

def rewrite_edges_file(updated_edge: dict | None = None):
    """Rewrite the edges file with current state (used after updates).
    
    Args:
        updated_edge: Optional edge that was modified in memory to include in the rewrite.
    """
    edges_path = _edges_path()
    if not os.path.exists(edges_path):
        return
    
    # Read all edges, track unique by (source_id, target_id, type)
    seen = {}
    with open(edges_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                edge = json.loads(line)
                key = (edge.get("source_id"), edge.get("target_id"), edge.get("type"))
                seen[key] = edge
            except json.JSONDecodeError:
                continue
    
    # If there's an updated edge, include it in the rewrite
    if updated_edge:
        key = (updated_edge.get("source_id"), updated_edge.get("target_id"), updated_edge.get("type"))
        seen[key] = updated_edge
    
    # PATCH 10.D.11: Write back unique edges in deterministic order (sorted)
    sorted_edges = sorted(seen.values(), key=lambda e: (e.get("source_id", ""), e.get("target_id", ""), e.get("type", "")))
    # PATCH 10.B.7: Write with lock
    with FileLock(edges_path):
        with open(edges_path, "w") as f:
            for edge in sorted_edges:
                f.write(json.dumps(edge, separators=(",", ":")) + "\n")

def migrate_links_to_edges():
    """One-time migration: extract embedded links[] to edge store."""
    # This would iterate all beads, find those with embedded "links",
    # and create edges in the edge store. Run once, then stop writing embedded links.
    pass  # TODO: implement if needed

def read_beads(filepath: str) -> list[dict]:
    """Read all beads from a JSONL file."""
    if not os.path.exists(filepath):
        return []
    beads = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                beads.append(json.loads(line))
    return beads

def read_all_beads() -> list[dict]:
    """Read all beads from all JSONL files in BEADS_DIR."""
    all_beads = []
    if not os.path.exists(BEADS_DIR):
        return all_beads
    for fname in sorted(os.listdir(BEADS_DIR)):
        if fname.endswith(".jsonl"):
            all_beads.extend(read_beads(os.path.join(BEADS_DIR, fname)))
    return all_beads

# ── Bead Construction ─────────────────────────────────────────────────

def make_bead(
    bead_type: str,
    title: str,
    summary: list[str] | str | None = None,
    detail: str | None = None,
    session_id: str | None = None,
    turn_refs: list[str] | None = None,
    scope: str = "personal",
    authority: str = "agent_inferred",
    confidence: float = 0.7,
    tags: list[str] | None = None,
    links: dict | None = None,
    evidence_refs: list[dict] | None = None,
    status: str = "open",
    # New structural metadata fields
    mechanism: str | None = None,
    impact_level: str | None = None,  # low, medium, high, existential
    uncertainty: float | None = None,  # 0.0-1.0
    # Contrast fields
    what_almost_happened: str | None = None,
    what_was_rejected: str | None = None,
    what_felt_risky: str | None = None,
    assumption: str | None = None,
) -> dict:
    """Construct a canonical bead object."""
    if bead_type not in BEAD_TYPES:
        raise ValueError(f"Unknown bead type: {bead_type}. Valid: {sorted(BEAD_TYPES)}")
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status}. Valid: {sorted(STATUSES)}")
    if impact_level and impact_level not in ("low", "medium", "high", "existential"):
        raise ValueError(f"Invalid impact_level: {impact_level}. Valid: low, medium, high, existential")
    if uncertainty is not None and not (0.0 <= uncertainty <= 1.0):
        raise ValueError(f"Invalid uncertainty: {uncertainty}. Must be 0.0-1.0")

    if isinstance(summary, str):
        summary = [summary]

    bead = {
        "id": f"bead-{generate_ulid()}",
        "type": bead_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "status": status,
    }

    if session_id:
        bead["session_id"] = session_id
    if turn_refs:
        bead["turn_refs"] = turn_refs
    if summary:
        bead["summary"] = summary
    if detail:
        bead["detail"] = detail

    bead["scope"] = scope
    bead["authority"] = authority
    bead["confidence"] = confidence

    # New structural fields
    if mechanism:
        bead["mechanism"] = mechanism
    if impact_level:
        bead["impact_level"] = impact_level
    if uncertainty is not None:
        bead["uncertainty"] = uncertainty
    
    # Contrast fields
    if what_almost_happened:
        bead["what_almost_happened"] = what_almost_happened
    if what_was_rejected:
        bead["what_was_rejected"] = what_was_rejected
    if what_felt_risky:
        bead["what_felt_risky"] = what_felt_risky
    if assumption:
        bead["assumption"] = assumption

    if links:
        # Validate link types
        for k in links:
            if k not in LINK_TYPES:
                raise ValueError(f"Unknown link type: {k}. Valid: {sorted(LINK_TYPES)}")
        bead["links"] = links
        
        # Create edges in edge store (first-class links)
        # Store links in bead for backward compatibility, but also create edges
        # Edge direction: source = cause (prior bead), target = effect (new bead)
        # "A follows B" means B caused A, so edge: source=B, target=A
        created_edges = []
        for link_type, target_ids in links.items():
            if isinstance(target_ids, list):
                for target_id in target_ids:
                    try:
                        # Edge direction per spec: source=current bead, target=linked bead
                        # "A derives-from B" means A was built on B: source=A, target=B
                        edge = add_edge(bead["id"], target_id, link_type, scope="session")
                        created_edges.append(edge)
                    except ValueError as e:
                        # Log but don't fail bead creation
                        print(f"Warning: Could not create edge: {e}", file=sys.stderr)
            else:
                # Single target
                try:
                    edge = add_edge(bead["id"], target_ids, link_type, scope="session")
                    created_edges.append(edge)
                except ValueError as e:
                    print(f"Warning: Could not create edge: {e}", file=sys.stderr)
        
        if created_edges:
            bead["_edge_count"] = len(created_edges)
    
    if evidence_refs:
        bead["evidence_refs"] = evidence_refs
    if tags:
        bead["tags"] = tags

    return bead

# ── Commands ──────────────────────────────────────────────────────────

def cmd_create(args):
    """Create a new bead."""
    summary = args.summary if args.summary else None
    tags = args.tags.split(",") if args.tags else None
    links = json.loads(args.links) if args.links else None
    evidence = json.loads(args.evidence) if args.evidence else None

    bead = make_bead(
        bead_type=args.type,
        title=args.title,
        summary=summary,
        detail=args.detail,
        session_id=args.session,
        turn_refs=args.turn_refs.split(",") if args.turn_refs else None,
        scope=args.scope or "personal",
        authority=args.authority or "agent_inferred",
        confidence=float(args.confidence) if args.confidence else 0.7,
        tags=tags,
        links=links,
        evidence_refs=evidence,
        status=args.status or "open",
        mechanism=args.mechanism,
        impact_level=args.impact,
        uncertainty=float(args.uncertainty) if args.uncertainty else None,
        what_almost_happened=args.almost,
        what_was_rejected=args.rejected,
        what_felt_risky=args.risky,
        assumption=args.assumption,
    )

    filepath = append_bead(bead)
    print(json.dumps({"ok": True, "id": bead["id"], "file": filepath}, indent=2))

def cmd_query(args):
    """Query beads with filters."""
    index = load_index()
    results = []

    for bead_id, meta in index["beads"].items():
        if args.type and meta["type"] != args.type:
            continue
        if args.session and meta.get("session_id") != args.session:
            continue
        if args.status and meta["status"] != args.status:
            continue
        if args.scope and meta.get("scope") != args.scope:
            continue
        if args.tag and args.tag not in meta.get("tags", []):
            continue
        results.append({"id": bead_id, **meta})

    # Sort by created_at descending
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    limit = int(args.limit) if args.limit else 20
    results = results[:limit]

    if args.full:
        # Load full beads from JSONL
        all_beads = {b["id"]: b for b in read_all_beads()}
        results = [all_beads[r["id"]] for r in results if r["id"] in all_beads]

    print(json.dumps(results, indent=2))

def cmd_link(args):
    """Add a link between two beads by appending a link-event bead."""
    if args.link_type not in LINK_TYPES:
        print(json.dumps({"ok": False, "error": f"Unknown link type: {args.link_type}"}))
        sys.exit(1)

    index = load_index()
    if args.source not in index["beads"]:
        print(json.dumps({"ok": False, "error": f"Source bead not found: {args.source}"}))
        sys.exit(1)
    if args.target not in index["beads"]:
        print(json.dumps({"ok": False, "error": f"Target bead not found: {args.target}"}))
        sys.exit(1)

    # We record links as a lightweight association bead
    bead = make_bead(
        bead_type="association",
        title=f"link: {args.source} --{args.link_type}--> {args.target}",
        summary=[f"{args.link_type} relationship"],
        links={args.link_type: [args.target]},
        session_id=index["beads"][args.source].get("session_id"),
        scope="personal",
        authority="agent_inferred",
        confidence=0.9,
        status="open",
    )
    # Also store source ref
    bead["source_bead"] = args.source
    bead["target_bead"] = args.target
    bead["relationship"] = args.link_type

    filepath = append_bead(bead)
    print(json.dumps({"ok": True, "id": bead["id"], "link": f"{args.source} --{args.link_type}--> {args.target}"}))

def cmd_close(args):
    """Close/update status of a bead. Appends a status-change record."""
    index = load_index()
    if args.id not in index["beads"]:
        print(json.dumps({"ok": False, "error": f"Bead not found: {args.id}"}))
        sys.exit(1)
    if args.status not in STATUSES:
        print(json.dumps({"ok": False, "error": f"Invalid status: {args.status}"}))
        sys.exit(1)

    # Update index
    index["beads"][args.id]["status"] = args.status
    save_index(index)

    # Append a status-change event to the bead's session file
    meta = index["beads"][args.id]
    session_id = meta.get("session_id")
    filepath = _session_file(session_id) if session_id else _global_file()

    event = {
        "event": "status_change",
        "bead_id": args.id,
        "new_status": args.status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with FileLock(filepath):
        with open(filepath, "a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")

    # Rebuild session metadata after status change
    rebuild_sessions()
    print(json.dumps({"ok": True, "id": args.id, "status": args.status}))

def cmd_compact(args):
    """Compact beads: replace full beads with minimal stubs in the working index."""
    all_beads = read_all_beads()
    index = load_index()
    compacted = 0

    for bead in all_beads:
        if bead.get("event"):  # skip events
            continue
        bead_id = bead["id"]
        meta = index["beads"].get(bead_id)
        if not meta:
            continue

        # Skip already compacted or promoted
        if meta["status"] in ("compacted", "promoted"):
            continue

        # Filter logic
        if args.session and bead.get("session_id") != args.session:
            continue
        if args.before:
            if bead["created_at"] >= args.before:
                continue
        if args.keep_promoted and meta["status"] == "promoted":
            continue

        # Compact: update index to compacted status
        index["beads"][bead_id]["status"] = "compacted"
        index["beads"][bead_id]["compacted_at"] = datetime.now(timezone.utc).isoformat()
        compacted += 1

    save_index(index)
    # Rebuild session metadata after bulk operation
    rebuild_sessions()
    print(json.dumps({"ok": True, "compacted": compacted}))

def cmd_uncompact(args):
    """Restore full-fidelity beads from archive."""
    all_beads = read_all_beads()
    bead_map = {}
    for b in all_beads:
        if not b.get("event"):
            bead_map[b["id"]] = b

    if args.id not in bead_map:
        print(json.dumps({"ok": False, "error": f"Bead not found in archive: {args.id}"}))
        sys.exit(1)

    target = bead_map[args.id]
    results = [target]

    if args.radius:
        radius = int(args.radius)
        # Find beads in the same session, ordered by creation time
        session_id = target.get("session_id")
        if session_id:
            session_beads = sorted(
                [b for b in bead_map.values() if b.get("session_id") == session_id],
                key=lambda b: b["created_at"]
            )
            idx = next((i for i, b in enumerate(session_beads) if b["id"] == args.id), None)
            if idx is not None:
                start = max(0, idx - radius)
                end = min(len(session_beads), idx + radius + 1)
                results = session_beads[start:end]

    # Also follow causal links if present
    if args.follow_links:
        linked_ids = set()
        for bead in results:
            for link_type, refs in bead.get("links", {}).items():
                if isinstance(refs, list):
                    linked_ids.update(refs)
                elif refs:
                    linked_ids.add(refs)
        for lid in linked_ids:
            if lid in bead_map and lid not in {b["id"] for b in results}:
                results.append(bead_map[lid])

    print(json.dumps(results, indent=2))

def cmd_recall(args):
    """Record that a bead was recalled (boosts myelination score).
    
    PATCH 2: Now writes event to session file for rebuild parity.
    """
    index = load_index()
    if args.id not in index["beads"]:
        print(json.dumps({"ok": False, "error": f"Bead not found: {args.id}"}))
        sys.exit(1)

    meta = index["beads"][args.id]
    meta["recall_count"] = meta.get("recall_count", 0) + 1
    meta["last_recalled"] = datetime.now(timezone.utc).isoformat()
    
    # PATCH 2: Write event for rebuild parity
    filepath = _session_file(meta.get("session_id")) if meta.get("session_id") else _global_file()
    event = {
        "event": "recalled",    # PATCH 2: normalized event name
        "bead_id": args.id,      # PATCH 2: standardized field name
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with FileLock(filepath):
        with open(filepath, "a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")
    
    save_index(index)

    print(json.dumps({
        "ok": True, "id": args.id,
        "recall_count": meta["recall_count"],
        "last_recalled": meta["last_recalled"]
    }))


def cmd_supersede(args):
    """Mark a bead as superseded by another.
    
    PATCH 2: Uses normalized event schema:
    - event: "superseded" (not "supersede")
    - old_id, new_id (not old_bead/new_bead)
    """
    index = load_index()
    if args.old not in index["beads"]:
        print(json.dumps({"ok": False, "error": f"Old bead not found: {args.old}"}))
        sys.exit(1)
    if args.new not in index["beads"]:
        print(json.dumps({"ok": False, "error": f"New bead not found: {args.new}"}))
        sys.exit(1)

    # Mark old as superseded
    index["beads"][args.old]["status"] = "superseded"
    index["beads"][args.old]["superseded_by"] = args.new
    index["beads"][args.old]["superseded_at"] = datetime.now(timezone.utc).isoformat()

    # Record event in the session file (PATCH 2: normalized schema)
    old_meta = index["beads"][args.old]
    filepath = _session_file(old_meta.get("session_id")) if old_meta.get("session_id") else _global_file()
    event = {
        "event": "superseded",  # PATCH 2: normalized to "superseded"
        "old_id": args.old,      # PATCH 2: normalized to old_id
        "new_id": args.new,      # PATCH 2: normalized to new_id
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with FileLock(filepath):
        with open(filepath, "a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")

    save_index(index)
    # Rebuild session metadata after status change
    rebuild_sessions()
    print(json.dumps({"ok": True, "old": args.old, "new": args.new, "status": "superseded"}))


def cmd_stats(args):
    """Show statistics about the bead store."""
    index = load_index()
    beads = index["beads"]

    by_type = {}
    by_status = {}
    by_session = {}
    for bead_id, meta in beads.items():
        t = meta["type"]
        s = meta["status"]
        sid = meta.get("session_id", "global")
        by_type[t] = by_type.get(t, 0) + 1
        by_status[s] = by_status.get(s, 0) + 1
        by_session[sid] = by_session.get(sid, 0) + 1

    stats = {
        "total_beads": len(beads),
        "sessions": len(index["sessions"]),
        "by_type": dict(sorted(by_type.items())),
        "by_status": dict(sorted(by_status.items())),
        "beads_per_session": dict(sorted(by_session.items(), key=lambda x: x[1], reverse=True)[:10]),
    }

    # File sizes
    total_bytes = 0
    if os.path.exists(BEADS_DIR):
        for f in os.listdir(BEADS_DIR):
            if f.endswith(".jsonl"):
                total_bytes += os.path.getsize(os.path.join(BEADS_DIR, f))
    stats["archive_bytes"] = total_bytes
    stats["archive_kb"] = round(total_bytes / 1024, 1)

    print(json.dumps(stats, indent=2))


def cmd_validate(args):
    """Validate store integrity.
    
    Checks:
    - Bead index consistency (duplicate IDs, missing files)
    - Edge store integrity (dangling IDs, cycles, types)
    - Session consistency (bead counts match)
    """
    issues = {
        "beads": {"duplicate_ids": [], "missing_files": [], "invalid_status": []},
        "edges": {"dangling_sources": [], "dangling_targets": [], "invalid_types": [], "self_loops": [], "cycles": [], "missing_class": []},
        "sessions": {"mismatched_counts": [], "missing_timestamps": []},
    }
    
    # Load index
    try:
        index = load_index()
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"Failed to load index: {e}"}))
        return
    
    bead_ids = set(index.get("beads", {}).keys())
    
    # Check beads
    for bead_id, bead in index.get("beads", {}).items():
        # Check file exists
        filepath = os.path.join(BEADS_DIR, bead.get("file", ""))
        if not os.path.exists(filepath):
            issues["beads"]["missing_files"].append({"bead_id": bead_id, "file": bead.get("file")})
        
        # Check status
        if bead.get("status") not in STATUSES:
            issues["beads"]["invalid_status"].append({"bead_id": bead_id, "status": bead.get("status")})
    
    # Check edges
    edges = get_all_edges()
    edge_bead_ids = set()
    for edge in edges:
        source = edge.get("source_id")
        target = edge.get("target_id")
        link_type = edge.get("type")
        
        edge_bead_ids.add(source)
        edge_bead_ids.add(target)
        
        # Self loop
        if source == target:
            issues["edges"]["self_loops"].append({"edge_id": edge.get("id"), "bead_id": source})
        
        # Dangling
        if source and source not in bead_ids:
            issues["edges"]["dangling_sources"].append({"edge_id": edge.get("id"), "bead_id": source})
        if target and target not in bead_ids:
            issues["edges"]["dangling_targets"].append({"edge_id": edge.get("id"), "bead_id": target})
        
        # Invalid type
        if link_type and link_type not in LINK_TYPES:
            issues["edges"]["invalid_types"].append({"edge_id": edge.get("id"), "type": link_type})
        
        # Missing edge_class
        if not edge.get("edge_class"):
            issues["edges"]["missing_class"].append({"edge_id": edge.get("id")})
    
    # Check cycles in protected types
    for edge in edges:
        if edge.get("type") in ACYCLIC_LINK_TYPES:
            try:
                if would_create_cycle(edge["source_id"], edge["target_id"], edge["type"]):
                    issues["edges"]["cycles"].append({
                        "edge_id": edge.get("id"),
                        "source": edge["source_id"],
                        "target": edge["target_id"],
                        "type": edge["type"],
                    })
            except RecursionError:
                pass
    
    # Check sessions
    for session_id, session in index.get("sessions", {}).items():
        expected = session.get("bead_count", 0)
        actual = len(session.get("bead_ids", []))
        if expected != actual:
            issues["sessions"]["mismatched_counts"].append({
                "session_id": session_id,
                "expected": expected,
                "actual": actual,
            })
        
        if not session.get("started_at"):
            issues["sessions"]["missing_timestamps"].append({"session_id": session_id, "field": "started_at"})
    
    # Count issues
    total_issues = (
        len(issues["beads"]["duplicate_ids"]) +
        len(issues["beads"]["missing_files"]) +
        len(issues["beads"]["invalid_status"]) +
        len(issues["edges"]["dangling_sources"]) +
        len(issues["edges"]["dangling_targets"]) +
        len(issues["edges"]["invalid_types"]) +
        len(issues["edges"]["self_loops"]) +
        len(issues["edges"]["cycles"]) +
        len(issues["sessions"]["mismatched_counts"])
    )
    
    result = {
        "ok": total_issues == 0,
        "total_beads": len(bead_ids),
        "total_edges": len(edges),
        "total_sessions": len(index.get("sessions", {})),
        "total_issues": total_issues,
        "issues": {k: v for k, v in issues.items() if any(x for x in v.values())},
    }
    
    print(json.dumps(result, indent=2))


def rebuild_sessions():
    """Rebuild session metadata from bead index entries.
    
    This fixes session bead_counts and bead_ids that can drift
    after bulk operations. Called automatically after compact/supersede.
    """
    index = load_index()
    
    # Reset session tracking
    sessions = {}
    
    # Rebuild from bead index
    for bead_id, bead_meta in index.get("beads", {}).items():
        sid = bead_meta.get("session_id")
        if not sid:
            continue
            
        if sid not in sessions:
            sessions[sid] = {
                "file": bead_meta.get("file", ""),
                "bead_count": 0,
                "bead_ids": [],
                "started_at": bead_meta.get("created_at", ""),
                "ended_at": "",
                "estimated_token_footprint": 0,
            }
        
        sessions[sid]["bead_count"] += 1
        sessions[sid]["bead_ids"].append(bead_id)
        
        # Update time bounds
        created = bead_meta.get("created_at", "")
        if created:
            if not sessions[sid]["started_at"] or created < sessions[sid]["started_at"]:
                sessions[sid]["started_at"] = created
            if not sessions[sid]["ended_at"] or created > sessions[sid]["ended_at"]:
                sessions[sid]["ended_at"] = created
            
    # Recalculate token footprints
    for sid, session in sessions.items():
        session["estimated_token_footprint"] = sum(
            estimate_tokens({"status": index["beads"].get(bid, {}).get("status", "open")})
            for bid in session.get("bead_ids", [])
        )
    
    index["sessions"] = sessions
    save_index(index)
    return sessions


def cmd_rebuild_index(args):
    """Rebuild the index from all JSONL files.
    
    PATCH 2: Now uses normalized event schema:
    - superseded: {event: "superseded", old_id, new_id}
    - recalled: {event: "recalled", bead_id}
    - status_change: {event: "status_change", bead_id, new_status}
    - pinned: {event: "pinned", bead_id, pinned}
    
    Handles both old and new schema for backward compatibility.
    """
    index = {"beads": {}, "sessions": {}, "version": 1}

    if not os.path.exists(MEMBEADS_ROOT):
        print(json.dumps({"ok": True, "beads": 0}))
        return

    count = 0
    events_replayed = {"status_change": 0, "superseded": 0, "pinned": 0, "recalled": 0}
    
    # First pass: Index all beads (skip event-only lines)
    for fname in sorted(os.listdir(MEMBEADS_ROOT)):
        if not fname.endswith(".jsonl"):
            continue
        filepath = os.path.join(MEMBEADS_ROOT, fname)
        line_num = 0
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    line_num += 1
                    continue
                # Skip pure event lines (they have "event" key but no "id")
                if obj.get("event") and "id" not in obj:
                    line_num += 1
                    continue
                # Index actual beads
                if "id" in obj:
                    index_bead(index, obj, fname, line_num)
                    count += 1
                line_num += 1

    # Second pass: Replay all events
    for fname in sorted(os.listdir(MEMBEADS_ROOT)):
        if not fname.endswith(".jsonl"):
            continue
        filepath = os.path.join(MEMBEADS_ROOT, fname)
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                event_type = obj.get("event")
                if not event_type:
                    continue
                
                # PATCH 2: Handle normalized superseded event schema
                if event_type == "superseded":
                    # New schema: old_id, new_id
                    old_id = obj.get("old_id")
                    new_id = obj.get("new_id")
                    if old_id and old_id in index["beads"]:
                        index["beads"][old_id]["status"] = "superseded"
                        index["beads"][old_id]["superseded_by"] = new_id
                        index["beads"][old_id]["superseded_at"] = obj.get("timestamp")
                        events_replayed["superseded"] += 1
                    # Also handle old schema for backward compatibility
                    bead_id = obj.get("bead_id") or obj.get("old_bead")
                    if bead_id and bead_id in index["beads"]:
                        index["beads"][bead_id]["status"] = "superseded"
                        index["beads"][bead_id]["superseded_by"] = obj.get("superseded_by") or obj.get("new_bead")
                        events_replayed["superseded"] += 1
                    continue
                
                # Handle bead_id lookup for other events
                bead_id = obj.get("bead_id")
                if bead_id and bead_id not in index["beads"]:
                    continue
                
                # Replay event
                if event_type == "status_change":
                    new_status = obj.get("new_status")
                    if new_status and bead_id:
                        index["beads"][bead_id]["status"] = new_status
                        index["beads"][bead_id]["status_changed_at"] = obj.get("changed_at")
                        if obj.get("reason"):
                            index["beads"][bead_id]["status_reason"] = obj["reason"]
                        events_replayed["status_change"] += 1
                
                elif event_type == "pinned":
                    if bead_id:
                        index["beads"][bead_id]["pinned"] = obj.get("pinned", True)
                        index["beads"][bead_id]["pinned_at"] = obj.get("pinned_at")
                        events_replayed["pinned"] += 1
                
                elif event_type == "recalled":
                    # PATCH 2: Handle both new and old schema
                    if bead_id:
                        current = index["beads"][bead_id].get("recall_count", 0)
                        index["beads"][bead_id]["recall_count"] = current + 1
                        index["beads"][bead_id]["last_recalled"] = obj.get("timestamp") or obj.get("recalled_at")
                        events_replayed["recalled"] += 1

    save_index(index)
    print(json.dumps({
        "ok": True, 
        "beads": count,
        "events_replayed": events_replayed
    }))


# PATCH 8: Myelinate command - decay/prune derived edges
MYELINATION_THRESHOLDS = {
    "weak": {"strength": 0.3, "retrievals": 2},
    "prune": {"strength": 0.1, "retrievals": 1},
}


def cmd_myelinate(args):
    """Myelinate derived edges: decay weak edges, prune very weak ones.
    
    PATCH 8: Dry-run shows proposed changes, --apply executes them.
    
    Derived edges accumulate retrieval stats. This command:
    - Decays strength on edges not retrieved recently
    - Promotes frequently used edges to stable tier
    - Prunes edges that fall below threshold
    """
    dry_run = not args.apply
    edges = get_all_edges()
    
    derived_edges = [e for e in edges if e.get("edge_class") == "derived"]
    
    now = datetime.now(timezone.utc)
    actions = []
    
    for edge in derived_edges:
        stats = edge.get("stats", {})
        retrieval_count = stats.get("retrieval_count", 0)
        strength = stats.get("strength", 1.0)
        tier = stats.get("tier", "fresh")
        
        # Check retrieval time for decay
        # PATCH 9.1 FIX: Keep both datetimes consistently timezone-aware (UTC)
        last_retrieved = stats.get("last_retrieved_at")
        days_since_retrieved = 30  # default if never retrieved
        if last_retrieved:
            try:
                # Parse as UTC-aware datetime
                last_time = datetime.fromisoformat(last_retrieved.replace("Z", "+00:00"))
                # Convert now to UTC as well for consistent subtraction
                now_utc = now.astimezone(timezone.utc)
                days_since_retrieved = (now_utc - last_time).days
            except (ValueError, OSError):
                days_since_retrieved = 30
        
        # Determine action
        action = None
        new_tier = tier
        new_strength = strength
        
        if retrieval_count >= 10:
            action = "promote"
            new_tier = "stable"
            new_strength = min(1.0, strength + 0.1)
        elif retrieval_count >= 3 and tier == "fresh":
            action = "promote"
            new_tier = "stable"
        elif days_since_retrieved > 30 and strength > 0.3:
            action = "decay"
            new_strength = max(0.0, strength - 0.2)
            if new_strength < 0.3:
                new_tier = "weak"
        elif (strength < 0.1 or days_since_retrieved > 90) and retrieval_count <= 1:
            action = "prune"
        
        if action:
            actions.append({
                "edge_id": edge["id"],
                "source": edge["source_id"],
                "target": edge["target_id"],
                "action": action,
                "old_tier": tier,
                "new_tier": new_tier,
                "old_strength": strength,
                "new_strength": new_strength,
            })
    
    if dry_run:
        print(json.dumps({
            "dry_run": True,
            "total_derived_edges": len(derived_edges),
            "edges_with_actions": len(actions),
            "actions": actions[:50],  # Limit output
        }, indent=2))
    else:
        # Apply changes
        pruned = 0
        updated = 0
        
        for action_item in actions:
            edge_id = action_item["edge_id"]
            action = action_item["action"]
            
            # Find and update edge
            for i, edge in enumerate(edges):
                if edge.get("id") == edge_id:
                    if action == "prune":
                        # Remove edge
                        edges.pop(i)
                        pruned += 1
                    else:
                        # Update stats
                        edge["stats"]["tier"] = action_item["new_tier"]
                        edge["stats"]["strength"] = action_item["new_strength"]
                        # PATCH 9.1 FIX: Use UTC-aware ISO format
                        edge["stats"]["last_retrieved_at"] = now.astimezone(timezone.utc).isoformat()
                        updated += 1
                    break
        
        # Write back
        edges_path = _edges_path()
        # PATCH 10.B.7: Write with lock
        with FileLock(edges_path):
            with open(edges_path, "w") as f:
                for edge in edges:
                    f.write(json.dumps(edge, separators=(",", ":")) + "\n")
        
        print(json.dumps({
            "dry_run": False,
            "edges_updated": updated,
            "edges_pruned": pruned,
            "actions": actions[:50],
        }, indent=2))


# ── CLI Parser ────────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(
        prog="mem-beads",
        description="Persistent causal agent memory with lossless compaction",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p = sub.add_parser("create", help="Create a new bead")
    p.add_argument("--type", required=True, choices=sorted(BEAD_TYPES))
    p.add_argument("--title", required=True)
    p.add_argument("--summary", nargs="+")
    p.add_argument("--detail")
    p.add_argument("--session")
    p.add_argument("--turn-refs")
    p.add_argument("--scope", choices=["personal", "project", "global"])
    p.add_argument("--authority", choices=["agent_inferred", "user_confirmed", "system"])
    p.add_argument("--confidence")
    p.add_argument("--tags")
    p.add_argument("--links", help="JSON object of link_type: [bead_ids]")
    p.add_argument("--evidence", help="JSON array of evidence refs")
    p.add_argument("--status", choices=sorted(STATUSES))
    # New structural fields
    p.add_argument("--mechanism", help="Short structural description of underlying logic")
    p.add_argument("--impact", choices=["low", "medium", "high", "existential"], help="Decision impact level")
    p.add_argument("--uncertainty", type=float, help="Uncertainty 0.0-1.0")
    # Contrast fields
    p.add_argument("--almost", help="What almost happened")
    p.add_argument("--rejected", help="What was considered but rejected")
    p.add_argument("--risky", help="What felt risky")
    p.add_argument("--assumption", help="What assumption was made")

    # query
    p = sub.add_parser("query", help="Query beads")
    p.add_argument("--type", choices=sorted(BEAD_TYPES))
    p.add_argument("--session")
    p.add_argument("--status", choices=sorted(STATUSES))
    p.add_argument("--scope", choices=["personal", "project", "global"])
    p.add_argument("--tag")
    p.add_argument("--limit", default="20")
    p.add_argument("--full", action="store_true", help="Return full bead objects, not just index entries")

    # link
    p = sub.add_parser("link", help="Link two beads")
    p.add_argument("--from", dest="source", required=True)
    p.add_argument("--to", dest="target", required=True)
    p.add_argument("--type", dest="link_type", required=True, choices=sorted(LINK_TYPES))

    # close
    p = sub.add_parser("close", help="Update bead status")
    p.add_argument("--id", required=True)
    p.add_argument("--status", required=True, choices=sorted(STATUSES))

    # compact
    p = sub.add_parser("compact", help="Compact beads")
    p.add_argument("--session", help="Compact only this session")
    p.add_argument("--before", help="Compact beads created before this ISO timestamp")
    p.add_argument("--keep-promoted", action="store_true")

    # uncompact
    p = sub.add_parser("uncompact", help="Restore full beads from archive")
    p.add_argument("--id", required=True)
    p.add_argument("--radius", help="Include N neighboring beads")
    p.add_argument("--follow-links", action="store_true")

    # recall
    p = sub.add_parser("recall", help="Record a bead recall (myelination)")
    p.add_argument("--id", required=True)

    # supersede
    p = sub.add_parser("supersede", help="Mark a bead as superseded by another")
    p.add_argument("--old", required=True, help="Bead being superseded")
    p.add_argument("--new", required=True, help="Bead that supersedes it")

    # stats
    sub.add_parser("stats", help="Show bead store statistics")
    
    # validate
    sub.add_parser("validate", help="Validate store integrity")

    # rebuild-index
    sub.add_parser("rebuild-index", help="Rebuild index from JSONL files")

    # myelinate (PATCH 8)
    p = sub.add_parser("myelinate", help="Decay/prune derived edges based on usage")
    p.add_argument("--apply", action="store_true", help="Actually apply changes (default is dry-run)")

    return parser

def main():
    parser = build_parser()
    args = parser.parse_args()

    commands = {
        "create": cmd_create,
        "query": cmd_query,
        "link": cmd_link,
        "close": cmd_close,
        "compact": cmd_compact,
        "uncompact": cmd_uncompact,
        "recall": cmd_recall,
        "supersede": cmd_supersede,
        "stats": cmd_stats,
        "validate": cmd_validate,
        "rebuild-index": cmd_rebuild_index,
        "myelinate": cmd_myelinate,  # PATCH 8
    }

    commands[args.command](args)

if __name__ == "__main__":
    main()
