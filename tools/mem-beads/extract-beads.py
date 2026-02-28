#!/usr/bin/env python3
"""
extract-beads.py: Extract bead markers from session transcript.

Runs post-session (via memoryFlush). Parses transcript JSONL for [[BEAD:...]] 
markers, writes valid beads to session .bd file via mem-beads CLI.

Usage:
    python3 extract-beads.py <session-id>
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Bead types from mem_beads.py
VALID_BEAD_TYPES = {
    "session_start", "session_end",
    "goal", "decision", "tool_call", "evidence",
    "outcome", "lesson", "checkpoint", "precedent",
    "context", "association",
    "promoted_lesson", "promoted_decision",
    "failed_hypothesis", "reversal", "misjudgment", 
    "overfitted_pattern", "abandoned_path",
    "reflection", "design_principle",
}

VALID_SCOPES = {"personal", "project", "global"}
VALID_AUTHORITIES = {"agent_inferred", "user_confirmed", "system"}

BEAD_MARKER = re.compile(r'\[\[BEAD:\s*(\{.*?\})\s*\]\]', re.DOTALL)

MEMBEADS_DIR = os.environ.get("MEMBEADS_DIR", "/home/node/.openclaw/workspace/.mem-beads")
MEMBEADS_CLI = "/home/node/.openclaw/workspace/tools/mem-beads/mem_beads.py"


def extract_beads_from_transcript(transcript_path: str) -> list[dict]:
    """Parse transcript JSONL for bead markers."""
    beads = []
    # No deduplication - beads are causal linear time series, duplicates may be intentional
    
    if not os.path.exists(transcript_path):
        print(f"No transcript found: {transcript_path}")
        return beads
    
    with open(transcript_path, 'r') as f:
        for line_num, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            
            # Look for assistant messages with bead markers
            content = ""
            if entry.get('role') == 'assistant':
                content = entry.get('content', '')
                # Also check tool calls if beads were in tool results
                for tc in entry.get('tool_calls', []):
                    if 'function' in tc:
                        content += ' ' + tc['function'].get('arguments', '')
            
            # Extract all bead markers
            matches = BEAD_MARKER.findall(content)
            for match in matches:
                try:
                    bead_data = json.loads(match)
                    
                    # Validate required fields
                    if 'type' not in bead_data:
                        print(f"Line {line_num}: Skipping bead - no type", file=sys.stderr)
                        continue
                    
                    bead_type = bead_data['type']
                    if bead_type not in VALID_BEAD_TYPES:
                        print(f"Line {line_num}: Skipping bead - invalid type: {bead_type}", file=sys.stderr)
                        continue
                    
                    # Keep all beads - causal linear time series, no dedup needed
                    beads.append(bead_data)
                    print(f"Extracted: [{bead_type}] {bead_data.get('title', 'untitled')}")
                    
                except json.JSONDecodeError as e:
                    print(f"Line {line_num}: Failed to parse bead JSON: {e}", file=sys.stderr)
                    continue
    
    return beads


def write_beads(beads: list[dict], session_id: str) -> int:
    """Write beads via mem-beads CLI."""
    written = 0
    
    for bead in beads:
        # Build CLI args
        args = [
            'python3', MEMBEADS_CLI, 'create',
            '--type', bead.get('type', 'context'),
            '--title', bead.get('title', 'Untitled')[:100],  # Limit title length
            '--session', session_id,
        ]
        
        # Optional fields
        if summary := bead.get('summary'):
            if isinstance(summary, list):
                args.extend(['--summary'] + [s[:500] for s in summary])  # Limit each summary item
            else:
                args.extend(['--summary', str(summary)[:500]])
        
        if scope := bead.get('scope'):
            if scope in VALID_SCOPES:
                args.extend(['--scope', scope])
        
        if authority := bead.get('authority'):
            if authority in VALID_AUTHORITIES:
                args.extend(['--authority', authority])
        
        if confidence := bead.get('confidence'):
            try:
                args.extend(['--confidence', str(float(confidence))])
            except (ValueError, TypeError):
                pass
        
        if tags := bead.get('tags'):
            if isinstance(tags, list):
                args.extend(['--tags', ','.join(tags)])
            else:
                args.extend(['--tags', str(tags)])
        
        # Execute
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode == 0:
            written += 1
            print(f"  ✓ Wrote: {bead.get('title', 'untitled')[:50]}")
        else:
            print(f"  ✗ Failed: {result.stderr.strip()}", file=sys.stderr)
    
    return written


def get_latest_session() -> str:
    """Find the most recent session file in .mem-beads/"""
    session_files = sorted(Path(MEMBEADS_DIR).glob("session-*.jsonl"), key=os.path.getmtime, reverse=True)
    if session_files:
        # Extract session ID from filename: session-main-2026-02-28.jsonl -> main-2026-02-28
        name = session_files[0].stem  # session-main-2026-02-28
        return name.replace("session-", "")
    return "main"


def main():
    # Get session ID from args or find latest
    if len(sys.argv) >= 2:
        session_id = sys.argv[1]
    else:
        session_id = get_latest_session()
        print(f"No session ID provided, using latest: {session_id}")
    
    transcript = f"{MEMBEADS_DIR}/session-{session_id}.jsonl"
    
    print(f"\n=== Extracting beads from session: {session_id} ===\n")
    
    beads = extract_beads_from_transcript(transcript)
    print(f"\nTotal bead markers found: {len(beads)}")
    
    if beads:
        written = write_beads(beads, session_id)
        print(f"\n=== Wrote {written} beads ===")
        
        # Optionally run consolidation after extraction
        if len(sys.argv) >= 3 and sys.argv[2] == '--consolidate':
            print("\nRunning consolidation...")
            result = subprocess.run([
                'python3', '/home/node/.openclaw/workspace/tools/mem-beads/consolidate.py',
                'consolidate', '--session', session_id, '--promote'
            ], capture_output=True, text=True)
            if result.returncode == 0:
                print("Consolidation complete")
            else:
                print(f"Consolidation failed: {result.stderr}", file=sys.stderr)
    else:
        print("No beads to write")


if __name__ == "__main__":
    main()
