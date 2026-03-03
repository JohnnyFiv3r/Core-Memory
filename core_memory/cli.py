"""
Core-Memory CLI.

Canonical command-line interface for core-memory.
Entry point only - does not export from __init__.py to avoid circular imports.
"""

import argparse
import json
from pathlib import Path

# Use relative import to avoid circular import
from .store import MemoryStore, DEFAULT_ROOT


def main():
    """CLI entry point for core-memory command."""
    parser = argparse.ArgumentParser(description="Core-Memory CLI")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="Memory root directory")
    
    subparsers = parser.add_subparsers(dest="command")
    
    # add command
    add_parser = subparsers.add_parser("add", help="Add a bead")
    add_parser.add_argument("--type", required=True, help="Bead type")
    add_parser.add_argument("--title", required=True, help="Bead title")
    add_parser.add_argument("--summary", nargs="*", help="Summary points")
    add_parser.add_argument("--because", nargs="*", help="Structured rationale points")
    add_parser.add_argument("--source-turn-ids", nargs="*", help="Provenance turn IDs")
    add_parser.add_argument("--tags", nargs="*", help="Tags")
    add_parser.add_argument("--session-id", help="Session ID")
    
    # query command
    query_parser = subparsers.add_parser("query", help="Query beads")
    query_parser.add_argument("--type", help="Filter by type")
    query_parser.add_argument("--status", help="Filter by status")
    query_parser.add_argument("--tags", nargs="*", help="Filter by tags")
    query_parser.add_argument("--limit", type=int, default=20)
    
    # stats command
    subparsers.add_parser("stats", help="Show statistics")
    
    # dream command
    subparsers.add_parser("dream", help="Run Dreamer analysis")
    
    # rebuild command
    subparsers.add_parser("rebuild", help="Rebuild index from events")

    # compact command
    compact_parser = subparsers.add_parser("compact", help="Compact beads")
    compact_parser.add_argument("--session", help="Compact only this session")
    compact_parser.add_argument("--promote", action="store_true", help="Promote compacted beads")

    # uncompact command
    uncompact_parser = subparsers.add_parser("uncompact", help="Restore compacted bead detail")
    uncompact_parser.add_argument("--id", required=True, help="Bead ID")

    # myelinate command
    myelinate_parser = subparsers.add_parser("myelinate", help="Run myelination analysis")
    myelinate_parser.add_argument("--apply", action="store_true", help="Apply changes (default dry-run)")

    # migrate-store command
    migrate_parser = subparsers.add_parser("migrate-store", help="Migrate legacy mem_beads store")
    migrate_parser.add_argument("--legacy-root", required=True, help="Path to legacy .mem-beads store")
    migrate_parser.add_argument("--no-backup", action="store_true", help="Disable backup before import")
    
    args = parser.parse_args()
    
    memory = MemoryStore(root=args.root)
    
    if args.command == "add":
        bead_id = memory.add_bead(
            type=args.type,
            title=args.title,
            summary=args.summary,
            because=args.because,
            source_turn_ids=args.source_turn_ids,
            tags=args.tags,
            session_id=args.session_id
        )
        print(f"Created bead: {bead_id}")
    
    elif args.command == "query":
        results = memory.query(
            type=args.type,
            status=args.status,
            tags=args.tags,
            limit=args.limit
        )
        for bead in results:
            print(f"{bead['id']}: [{bead['type']}] {bead['title']}")
    
    elif args.command == "stats":
        stats = memory.stats()
        print(json.dumps(stats, indent=2))
    
    elif args.command == "dream":
        results = memory.dream()
        print(json.dumps(results, indent=2))
    
    elif args.command == "rebuild":
        index = memory.rebuild_index()
        print(f"Rebuilt index with {index['stats']['total_beads']} beads")

    elif args.command == "compact":
        result = memory.compact(session_id=args.session, promote=args.promote)
        print(json.dumps(result))

    elif args.command == "uncompact":
        result = memory.uncompact(args.id)
        print(json.dumps(result))
        if not result.get("ok"):
            raise SystemExit(1)

    elif args.command == "myelinate":
        result = memory.myelinate(apply=args.apply)
        print(json.dumps(result))

    elif args.command == "migrate-store":
        result = memory.migrate_legacy_store(args.legacy_root, backup=not args.no_backup)
        print(json.dumps(result, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
