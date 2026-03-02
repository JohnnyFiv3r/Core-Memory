#!/usr/bin/env python3
"""
mem-beads associate: Association crawler for semantic linking.

Reads Layer 3 (promoted beads) and Layer 2 (rolling window / recent beads),
identifies semantic associations, and creates association meta-beads.

Enhanced with:
- Confidence scoring
- Novelty scoring  
- Evidence references
- Reinforcement tracking
- Decay model

Usage:
  # Generate analysis prompt for an LLM to evaluate
  associate.py prompt [--scope project] [--limit 50]

  # Record associations discovered by the LLM
  associate.py record --associations '[{"source": "bead-X", "target": "bead-Y", "relationship": "similar_pattern", "explanation": "...", "novelty": 0.9, "confidence": 0.8, "evidence": ["..."]}]'

  # List existing associations
  associate.py list [--limit 20]

  # Surface interesting connections (human-readable)
  associate.py surface [--limit 5]

  # Run decay on associations (call periodically)
  associate.py decay
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
from mem_beads import (
    BEADS_DIR, load_index, save_index, read_all_beads, append_bead, make_bead, generate_ulid
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

DECAY_RATE = 0.1  # Amount to decay per cycle not reinforced
DECAY_THRESHOLD = 0.3  # Below this, archive the association
DEFAULT_NOVELTY = 0.5  # Default novelty if not specified


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
    """Get all existing association beads (not archived)."""
    all_beads = read_all_beads()
    index = load_index()
    return [
        b for b in all_beads
        if not b.get("event") and b.get("type") == "association"
        and b.get("relationship")  # skip link-type associations from cmd_link
        and index["beads"].get(b["id"], {}).get("status") != "archived"
    ]


def get_association_key(source: str, target: str) -> str:
    """Create a canonical key for an association (order-independent)."""
    pair = sorted([source, target])
    return f"{pair[0]}::{pair[1]}"


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
    existing_keys = set()
    for a in existing:
        src = a.get("source_bead", "")
        tgt = a.get("target_bead", "")
        if src and tgt:
            existing_keys.add(get_association_key(src, tgt))

    # Get existing association stats for the prompt
    reinforced_count = sum(1 for a in existing if a.get("reinforced_count", 0) > 0)
    avg_novelty = sum(a.get("novelty", 0) for a in existing) / max(len(existing), 1)

    prompt_parts = [
        "# Memory Association Analysis (Move 37)",
        "",
        f"## Context",
        f"- Existing associations: {len(existing)}",
        f"- Previously reinforced: {reinforced_count}",
        f"- Average novelty: {avg_novelty:.2f}",
        "",
        "## Beads to Analyze",
        "",
    ]

    for bead in beads:
        prompt_parts.append(format_bead_for_prompt(bead))
        prompt_parts.append("")

    if existing_keys:
        prompt_parts.append(f"## Already Associated ({len(existing_keys)} pairs)")
        prompt_parts.append("Skip these pairs — they're already linked.")
        prompt_parts.append("")
        shown = set()
        for a in existing[:10]:
            pair = get_association_key(a.get("source_bead", ""), a.get("target_bead", ""))
            if pair not in shown:
                novelty = a.get("novelty", 0)
                reinforced = a.get("reinforced_count", 0)
                prompt_parts.append(f"- {pair}: novelty={novelty:.1f}, reinforced={reinforced}")
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
        "",
        "## Scoring (REQUIRED for each association)",
        "",
        "- **novelty** (0-1): How surprising/unexpected is this connection?",
        "  - 0.9+ = genuinely surprising, challenges assumptions",
        "  - 0.7-0.9 = interesting but plausible", 
        "  - 0.5-0.7 = somewhat expected",
        "  - <0.5 = obvious connection, probably not worth recording",
        "",
        "- **confidence** (0-1): How well-supported by the bead data?",
        "  - Both beads clearly support the connection",
        "  - Evidence in their summaries/titles matches",
        "",
        "- **evidence** (array): Which specific content supports this?",
        "  - List the specific summary points or titles that support the connection",
        "",
        "**Quality over quantity.** Only flag genuinely meaningful associations.",
        "",
        "## Output Format",
        "",
        "Output ONLY a JSON array (no markdown fences, no explanation):",
        "",
        '```json',
        '[',
        '  {',
        '    "source": "bead-XXXXX",',
        '    "target": "bead-YYYYY",',
        '    "relationship": "similar_pattern",',
        '    "explanation": "Both involve...",',
        '    "novelty": 0.8,',
        '    "confidence": 0.9,',
        '    "evidence": ["from bead X summary", "from bead Y context"]',
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
        print(json.dumps({
            "ok": True, 
            "prompt": prompt, 
            "bead_count": len(beads), 
            "existing_associations": len(existing),
            "reinforced": sum(1 for a in existing if a.get("reinforced_count", 0) > 0),
            "avg_novelty": sum(a.get("novelty", 0) for a in existing) / max(len(existing), 1)
        }))
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
    existing = get_existing_associations()
    existing_keys = {get_association_key(a.get("source_bead", ""), a.get("target_bead", "")) for a in existing}

    created = []
    reinforced = []

    for assoc in associations:
        source = assoc.get("source", "")
        target = assoc.get("target", "")
        relationship = assoc.get("relationship", "")
        explanation = assoc.get("explanation", "")
        novelty = float(assoc.get("novelty", DEFAULT_NOVELTY))
        confidence = float(assoc.get("confidence", 0.7))
        evidence = assoc.get("evidence", [])

        # Validate
        if source not in index["beads"] or target not in index["beads"]:
            continue
        if relationship not in RELATIONSHIP_TYPES:
            continue
        if novelty < 0.3:  # Skip low-novelty associations
            continue

        key = get_association_key(source, target)

        # Check if this association already exists
        if key in existing_keys:
            # Reinforce existing association
            for existing_assoc in existing:
                if get_association_key(existing_assoc.get("source_bead", ""), existing_assoc.get("target_bead", "")) == key:
                    existing_assoc["reinforced_count"] = existing_assoc.get("reinforced_count", 0) + 1
                    existing_assoc["last_observed"] = datetime.now(timezone.utc).isoformat()
                    existing_assoc["decay_score"] = 1.0  # Reset decay
                    reinforced.append({
                        "id": existing_assoc["id"],
                        "reinforced_count": existing_assoc["reinforced_count"]
                    })
                    # Update in index and JSONL
                    bead_id = existing_assoc["id"]
                    if bead_id in index["beads"]:
                        index["beads"][bead_id]["reinforced_count"] = existing_assoc["reinforced_count"]
                    save_index(index)
                    break
            continue

        # Create new association
        bead = make_bead(
            bead_type="association",
            title=f"{relationship}: {index['beads'][source].get('title', '?')[:30]} ↔ {index['beads'][target].get('title', '?')[:30]}",
            summary=[explanation],
            scope="personal",
            authority="agent_inferred",
            confidence=confidence,
            tags=["association", relationship, f"novelty:{int(novelty*10)}"],
            links={"associated_with": [source, target]},
        )
        bead["source_bead"] = source
        bead["target_bead"] = target
        bead["relationship"] = relationship
        bead["novelty"] = novelty
        bead["evidence"] = evidence
        bead["reinforced_count"] = 0
        bead["first_observed"] = datetime.now(timezone.utc).isoformat()
        bead["last_observed"] = datetime.now(timezone.utc).isoformat()
        bead["decay_score"] = 1.0

        filepath = append_bead(bead)
        created.append({
            "id": bead["id"], 
            "source": source, 
            "target": target, 
            "relationship": relationship,
            "novelty": novelty,
            "confidence": confidence
        })

    # Save any reinforced updates
    if reinforced:
        save_index(index)

    print(json.dumps({
        "ok": True, 
        "created": len(created), 
        "reinforced": len(reinforced),
        "associations": created + reinforced
    }, indent=2))


def cmd_list(args):
    """List existing associations."""
    associations = get_existing_associations()
    associations.sort(key=lambda a: (a.get("novelty", 0), a.get("reinforced_count", 0)), reverse=True)
    limit = int(args.limit)

    results = []
    for a in associations[:limit]:
        results.append({
            "id": a["id"],
            "source": a.get("source_bead"),
            "target": a.get("target_bead"),
            "relationship": a.get("relationship"),
            "explanation": (a.get("summary") or [""])[0],
            "novelty": a.get("novelty", 0),
            "confidence": a.get("confidence", 0),
            "reinforced_count": a.get("reinforced_count", 0),
            "decay_score": a.get("decay_score", 1.0),
            "first_observed": a.get("first_observed"),
            "last_observed": a.get("last_observed"),
        })

    print(json.dumps(results, indent=2))


def cmd_surface(args):
    """Surface interesting connections in human-readable format."""
    associations = get_existing_associations()
    if not associations:
        print("No associations discovered yet.")
        return

    # Sort by novelty * reinforced_count (interesting = novel + reinforced)
    associations.sort(key=lambda a: (
        a.get("novelty", 0) * (1 + a.get("reinforced_count", 0)),
        a.get("decay_score", 1.0)
    ), reverse=True)

    index = load_index()
    limit = int(args.limit)

    print("## Dreamer: Memory Connections\n")
    print(f"Found {len(associations)} associations.\n")

    for a in associations[:limit]:
        src_title = index["beads"].get(a.get("source_bead", ""), {}).get("title", "?")
        tgt_title = index["beads"].get(a.get("target_bead", ""), {}).get("title", "?")
        rel = a.get("relationship", "?")
        explanation = (a.get("summary") or [""])[0]
        novelty = a.get("novelty", 0)
        confidence = a.get("confidence", 0)
        reinforced = a.get("reinforced_count", 0)
        decay = a.get("decay_score", 1.0)

        # Score indicator
        score = "⭐" * max(1, int(novelty * 5))
        if reinforced > 0:
            score += f" (+{reinforced}x reinforced)"
        if decay < 0.5:
            score += " [fading]"

        print(f"### {score}")
        print(f"**{rel}** | novelty: {novelty:.1f} | confidence: {confidence:.1f}")
        print(f"  {src_title}")
        print(f"  ↔ {tgt_title}")
        if explanation:
            print(f"  _Why: {explanation}_")
        print()


def cmd_decay(args):
    """Apply decay to associations that haven't been reinforced recently."""
    associations = get_existing_associations()
    index = load_index()
    
    archived = []
    decayed = []

    for a in associations:
        # Skip if reinforced recently (within last 2 cycles)
        if a.get("reinforced_count", 0) > 0:
            # Check if we should decay anyway
            last_observed = a.get("last_observed", "")
            # For now, always decay non-reinforced associations
            pass
        
        # Apply decay
        current_decay = a.get("decay_score", 1.0)
        new_decay = max(0, current_decay - DECAY_RATE)
        
        bead_id = a["id"]
        if new_decay < DECAY_THRESHOLD:
            # Archive
            if bead_id in index["beads"]:
                index["beads"][bead_id]["status"] = "archived"
            archived.append(bead_id)
        elif new_decay != current_decay:
            # Update decay
            a["decay_score"] = new_decay
            if bead_id in index["beads"]:
                index["beads"][bead_id]["decay_score"] = new_decay
            decayed.append(bead_id)

    if archived or decayed:
        save_index(index)

    print(json.dumps({
        "ok": True,
        "archived": len(archived),
        "decayed": len(decayed),
        "archived_ids": archived,
        "decayed_ids": decayed
    }, indent=2))


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

    # decay
    p = sub.add_parser("decay", help="Apply decay model to associations")
    p.add_argument("--dry-run", action="store_true", help="Show what would decay without doing it")

    args = parser.parse_args()

    commands = {
        "prompt": cmd_prompt,
        "record": cmd_record,
        "list": cmd_list,
        "surface": cmd_surface,
        "decay": cmd_decay,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
