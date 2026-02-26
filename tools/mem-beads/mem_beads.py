#!/usr/bin/env python3
"""
mem-beads: Persistent causal agent memory with lossless compaction.

CLI for creating, querying, linking, compacting, and uncompacting beads.
Zero external dependencies — stdlib only.
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

BEADS_DIR = os.environ.get("MEMBEADS_DIR", os.path.join(
    os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace")),
    ".mem-beads"
))

INDEX_FILE = "index.json"
CROCKFORD_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

BEAD_TYPES = {
    "session_start", "session_end",
    "goal", "decision", "tool_call", "evidence",
    "outcome", "lesson", "checkpoint", "precedent",
    "context", "association",
    "promoted_lesson", "promoted_decision",
}

LINK_TYPES = {
    "caused_by", "led_to", "blocked_by", "unblocks",
    "supersedes", "superseded_by", "associated_with",
}

STATUSES = {"open", "closed", "promoted", "compacted", "superseded"}

PROMOTION_ELIGIBLE = BEAD_TYPES - {"session_start", "session_end", "checkpoint", "association"}

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
    return os.path.join(BEADS_DIR, INDEX_FILE)

def load_index() -> dict:
    path = _index_path()
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {"beads": {}, "sessions": {}, "version": 1}

def save_index(index: dict):
    path = _index_path()
    with open(path, "w") as f:
        json.dump(index, f, indent=2)

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
    }
    # Track sessions
    sid = bead.get("session_id")
    if sid:
        if sid not in index["sessions"]:
            index["sessions"][sid] = {"file": jsonl_file, "bead_count": 0}
        index["sessions"][sid]["bead_count"] += 1

# ── JSONL I/O ─────────────────────────────────────────────────────────

def _session_file(session_id: str) -> str:
    return os.path.join(BEADS_DIR, f"session-{session_id}.jsonl")

def _global_file() -> str:
    return os.path.join(BEADS_DIR, "global.jsonl")

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
) -> dict:
    """Construct a canonical bead object."""
    if bead_type not in BEAD_TYPES:
        raise ValueError(f"Unknown bead type: {bead_type}. Valid: {sorted(BEAD_TYPES)}")
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status}. Valid: {sorted(STATUSES)}")

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

    if links:
        # Validate link types
        for k in links:
            if k not in LINK_TYPES:
                raise ValueError(f"Unknown link type: {k}. Valid: {sorted(LINK_TYPES)}")
        bead["links"] = links
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

def cmd_rebuild_index(args):
    """Rebuild the index from all JSONL files."""
    index = {"beads": {}, "sessions": {}, "version": 1}

    if not os.path.exists(BEADS_DIR):
        print(json.dumps({"ok": True, "beads": 0}))
        return

    count = 0
    for fname in sorted(os.listdir(BEADS_DIR)):
        if not fname.endswith(".jsonl"):
            continue
        filepath = os.path.join(BEADS_DIR, fname)
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
                if obj.get("event"):  # skip status-change events
                    line_num += 1
                    continue
                if "id" in obj:
                    index_bead(index, obj, fname, line_num)
                    count += 1
                line_num += 1

    # Apply status changes
    for fname in sorted(os.listdir(BEADS_DIR)):
        if not fname.endswith(".jsonl"):
            continue
        filepath = os.path.join(BEADS_DIR, fname)
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("event") == "status_change":
                    bead_id = obj["bead_id"]
                    if bead_id in index["beads"]:
                        index["beads"][bead_id]["status"] = obj["new_status"]

    save_index(index)
    print(json.dumps({"ok": True, "beads": count}))

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

    # stats
    sub.add_parser("stats", help="Show bead store statistics")

    # rebuild-index
    sub.add_parser("rebuild-index", help="Rebuild index from JSONL files")

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
        "stats": cmd_stats,
        "rebuild-index": cmd_rebuild_index,
    }

    commands[args.command](args)

if __name__ == "__main__":
    main()
