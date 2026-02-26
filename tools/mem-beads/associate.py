#!/usr/bin/env python3
"""
mem-beads associate: Association crawler for semantic linking.

Reads Layer 3 (promoted beads) and Layer 2 (rolling window / recent beads),
identifies semantic associations, and creates association meta-beads.

Designed to be run by a sub-agent or cron job. Outputs a structured
analysis prompt for an LLM to evaluate, OR processes pre-computed associations.

Usage:
  # Generate analysis prompt for an LLM to evaluate
  associate.py prompt [--scope project] [--limit 50]

  # Record associations discovered by the LLM
  associate.py record --associations '[{"source": "bead-X", "target": "bead-Y", "relationship": "similar_pattern", "explanation": "..."}]'

  # List existing associations
  associate.py list [--limit 20]

  # Surface interesting connections (human-readable)
  associate.py surface [--limit 5]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from mem_beads import (
    BEADS_DIR, load_index, read_all_beads, append_bead, make_bead, generate_ulid
)

RELATIONSHIP_TYPES = {
    "similar_pattern",    # Same approach/strategy used in different contexts
    "same_mistake",       # Same error repeated across projects/sessions
    "transferable_lesson", # Insight from one domain applicable to another
    "contradicts",        # Two beads that conflict
    "reinforces",         # Two beads that support each other
    "generalizes",        # One bead is a generalization of another
    "specializes",        # One bead is a specific case of another
}


def get_associable_beads(scope: str | None = None, limit: int = 50) -> list[dict]:
    """Get beads eligible for association analysis."""
    index = load_index()
    all_beads_raw = read_all_beads()
    bead_map = {b["id"]: b for b in all_beads_raw if not b.get("event") and b.get("id")}

    candidates = []
    for bead_id, meta in index["beads"].items():
        # Only associate open or promoted beads
        if meta["status"] not in ("open", "promoted"):
            continue
        # Skip association beads themselves
        if meta["type"] == "association":
            continue
        # Skip session lifecycle
        if meta["type"] in ("session_start", "session_end", "checkpoint"):
            continue
        # Scope filter
        if scope and meta.get("scope") != scope:
            continue

        full = bead_map.get(bead_id)
        if full:
            # Attach index metadata
            full["_recall_count"] = meta.get("recall_count", 0)
            full["_status"] = meta["status"]
            candidates.append(full)

    # Sort: promoted first, then by recall count, then by creation time
    candidates.sort(key=lambda b: (
        0 if b.get("_status") == "promoted" else 1,
        -b.get("_recall_count", 0),
        b.get("created_at", ""),
    ))

    return candidates[:limit]


def get_existing_associations() -> list[dict]:
    """Get all existing association beads."""
    all_beads = read_all_beads()
    return [
        b for b in all_beads
        if not b.get("event") and b.get("type") == "association"
        and b.get("relationship")  # skip link-type associations from cmd_link
    ]


def format_bead_for_prompt(bead: dict) -> str:
    """Format a bead concisely for LLM analysis."""
    lines = [f"- **{bead['id']}** [{bead['type']}] {bead.get('title', 'untitled')}"]
    if bead.get("summary"):
        for s in bead["summary"][:3]:
            lines.append(f"  - {s}")
    tags = bead.get("tags", [])
    scope = bead.get("scope", "personal")
    if tags or scope != "personal":
        meta_parts = []
        if scope != "personal":
            meta_parts.append(f"scope={scope}")
        if tags:
            meta_parts.append(f"tags={','.join(tags)}")
        lines.append(f"  _({', '.join(meta_parts)})_")
    return "\n".join(lines)


def generate_prompt(beads: list[dict], existing: list[dict]) -> str:
    """Generate the analysis prompt for an LLM."""
    # Build existing association set to avoid duplicates
    existing_pairs = set()
    for a in existing:
        src = a.get("source_bead", "")
        tgt = a.get("target_bead", "")
        if src and tgt:
            existing_pairs.add((src, tgt))
            existing_pairs.add((tgt, src))

    prompt_parts = [
        "# Memory Association Analysis",
        "",
        "Review these memory beads and identify meaningful semantic associations between them.",
        "",
        "## Beads to Analyze",
        "",
    ]

    for bead in beads:
        prompt_parts.append(format_bead_for_prompt(bead))
        prompt_parts.append("")

    if existing_pairs:
        prompt_parts.append(f"## Already Associated ({len(existing_pairs) // 2} pairs)")
        prompt_parts.append("Skip these pairs — they're already linked.")
        prompt_parts.append("")
        shown = set()
        for a in existing[:20]:
            pair = (a.get("source_bead", ""), a.get("target_bead", ""))
            if pair not in shown:
                prompt_parts.append(f"- {pair[0]} ↔ {pair[1]}: {a.get('relationship', '?')}")
                shown.add(pair)
        prompt_parts.append("")

    prompt_parts.extend([
        "## Instructions",
        "",
        "Find NEW associations (not already listed above). For each:",
        "",
        "1. Identify two beads that are semantically related",
        "2. Classify the relationship:",
        f"   Valid types: {', '.join(sorted(RELATIONSHIP_TYPES))}",
        "3. Write a brief explanation of WHY they're related",
        "4. Rate confidence (0.5-1.0)",
        "",
        "**Quality over quantity.** Only flag genuinely meaningful associations —",
        "things that would help an agent connect the dots in a future session.",
        "Skip obvious/trivial links (e.g. beads in the same session about the same task).",
        "",
        "## Output Format",
        "",
        "Output ONLY a JSON array (no markdown fences, no explanation):",
        "",
        '```',
        '[',
        '  {',
        '    "source": "bead-XXXXX",',
        '    "target": "bead-YYYYY",',
        '    "relationship": "similar_pattern",',
        '    "explanation": "Both involve...",',
        '    "confidence": 0.8',
        '  }',
        ']',
        '```',
        "",
        "If no meaningful NEW associations found, output: []",
    ])

    return "\n".join(prompt_parts)


def cmd_prompt(args):
    """Generate analysis prompt for LLM."""
    beads = get_associable_beads(scope=args.scope, limit=int(args.limit))
    existing = get_existing_associations()

    if len(beads) < 2:
        print(json.dumps({"ok": True, "message": "Not enough beads to associate", "bead_count": len(beads)}))
        return

    prompt = generate_prompt(beads, existing)
    if args.json:
        print(json.dumps({"ok": True, "prompt": prompt, "bead_count": len(beads), "existing_associations": len(existing)}))
    else:
        print(prompt)


def cmd_record(args):
    """Record associations discovered by the LLM."""
    try:
        associations = json.loads(args.associations)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"Invalid JSON: {e}"}))
        sys.exit(1)

    if not isinstance(associations, list):
        print(json.dumps({"ok": False, "error": "Expected JSON array"}))
        sys.exit(1)

    index = load_index()
    created = []

    for assoc in associations:
        source = assoc.get("source", "")
        target = assoc.get("target", "")
        relationship = assoc.get("relationship", "")
        explanation = assoc.get("explanation", "")
        confidence = float(assoc.get("confidence", 0.7))

        # Validate
        if source not in index["beads"] or target not in index["beads"]:
            continue
        if relationship not in RELATIONSHIP_TYPES:
            continue

        bead = make_bead(
            bead_type="association",
            title=f"{relationship}: {index['beads'][source].get('title', '?')[:30]} ↔ {index['beads'][target].get('title', '?')[:30]}",
            summary=[explanation],
            scope="personal",
            authority="agent_inferred",
            confidence=confidence,
            tags=["association", relationship],
            links={"associated_with": [source, target]},
        )
        bead["source_bead"] = source
        bead["target_bead"] = target
        bead["relationship"] = relationship

        filepath = append_bead(bead)
        created.append({"id": bead["id"], "source": source, "target": target, "relationship": relationship})

    print(json.dumps({"ok": True, "created": len(created), "associations": created}, indent=2))


def cmd_list(args):
    """List existing associations."""
    associations = get_existing_associations()
    associations.sort(key=lambda a: a.get("created_at", ""), reverse=True)
    limit = int(args.limit)

    results = []
    for a in associations[:limit]:
        results.append({
            "id": a["id"],
            "source": a.get("source_bead"),
            "target": a.get("target_bead"),
            "relationship": a.get("relationship"),
            "explanation": (a.get("summary") or [""])[0],
            "confidence": a.get("confidence"),
            "created_at": a.get("created_at"),
        })

    print(json.dumps(results, indent=2))


def cmd_surface(args):
    """Surface interesting connections in human-readable format."""
    associations = get_existing_associations()
    if not associations:
        print("No associations discovered yet.")
        return

    index = load_index()
    associations.sort(key=lambda a: a.get("confidence", 0), reverse=True)
    limit = int(args.limit)

    print("## Interesting Connections\n")
    for a in associations[:limit]:
        src_title = index["beads"].get(a.get("source_bead", ""), {}).get("title", "?")
        tgt_title = index["beads"].get(a.get("target_bead", ""), {}).get("title", "?")
        rel = a.get("relationship", "?")
        explanation = (a.get("summary") or [""])[0]
        conf = a.get("confidence", 0)

        print(f"**{rel}** (confidence: {conf})")
        print(f"  {src_title}")
        print(f"  ↔ {tgt_title}")
        if explanation:
            print(f"  _Why: {explanation}_")
        print()


def main():
    parser = argparse.ArgumentParser(prog="mem-beads-associate")
    sub = parser.add_subparsers(dest="command", required=True)

    # prompt
    p = sub.add_parser("prompt", help="Generate analysis prompt for LLM")
    p.add_argument("--scope", choices=["personal", "project", "global"])
    p.add_argument("--limit", default="50")
    p.add_argument("--json", action="store_true")

    # record
    p = sub.add_parser("record", help="Record associations from LLM analysis")
    p.add_argument("--associations", required=True, help="JSON array of associations")

    # list
    p = sub.add_parser("list", help="List existing associations")
    p.add_argument("--limit", default="20")

    # surface
    p = sub.add_parser("surface", help="Surface interesting connections")
    p.add_argument("--limit", default="5")

    args = parser.parse_args()
    commands = {
        "prompt": cmd_prompt,
        "record": cmd_record,
        "list": cmd_list,
        "surface": cmd_surface,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
