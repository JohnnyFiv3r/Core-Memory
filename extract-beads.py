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

# Support both old HTML comment format and new invisible {::bead ... /::} format
BEAD_MARKER = re.compile(r'(?:<!--\s*BEAD:\s*(\{.*?\})\s*-->|\{::bead\s+(.*?)\s*/::\})', re.DOTALL)

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/home/node/.openclaw/workspace")
MEMBEADS_DIR = os.environ.get("MEMBEADS_DIR", f"{WORKSPACE}/.mem-beads")
CORE_MEMORY_CLI = os.environ.get("CORE_MEMORY_CLI", "core-memory")

# OpenClaw stores session transcripts in agents/<agentId>/sessions/
def get_latest_transcript(agent_id: str = "main") -> tuple[str, str]:
    """Find the most recent session transcript. Returns (session_id, path)."""
    sessions_dir = Path(f"/home/node/.openclaw/agents/{agent_id}/sessions")
    
    if not sessions_dir.exists():
        return get_latest_session(), f"{MEMBEADS_DIR}/session-{get_latest_session()}.jsonl"
    
    # Find most recent JSONL file (not sessions.json)
    transcript_files = sorted(
        [f for f in sessions_dir.glob("*.jsonl") if f.name != "sessions.json"],
        key=os.path.getmtime,
        reverse=True
    )
    
    if transcript_files:
        # Extract session ID from filename: 36bf908b-fa65-4b9d-aaa8-7410e668d0d7.jsonl -> 36bf908b-fa65-4b9d-aaa8-7410e668d0d7
        session_id = transcript_files[0].stem
        return session_id, str(transcript_files[0])
    
    # Fallback
    return get_latest_session(), f"{MEMBEADS_DIR}/session-{get_latest_session()}.jsonl"


def _parse_attribute_format(attr_string: str) -> dict:
    """Parse {::bead type="..." title="..." /::} format into dict."""
    import re
    bead = {}
    
    # Match key="value" pairs (supports both single and double quotes)
    pattern = r'(\w+)="([^"]*)"'
    for match in re.finditer(pattern, attr_string):
        key, value = match.groups()
        
        # Convert some keys
        if key == "type":
            bead["type"] = value
        elif key == "turn":
            bead["turn"] = int(value)
        elif key == "summary":
            # Split by | into list
            bead["summary"] = value.split("|") if value else []
        else:
            bead[key] = value
    
    return bead


def extract_beads_from_transcript(transcript_path: str) -> list[dict]:
    """Parse transcript JSONL for bead markers.
    
    Supports two formats:
    1. Simple: {"role": "assistant", "content": "..."}
    2. Nested (OpenClaw): {"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "..."}]}}
    """
    beads = []
    
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
            
            # Extract assistant message content (supports both formats)
            content = ""
            
            # Format 1: Simple {"role": "assistant", "content": "..."}
            if entry.get('role') == 'assistant':
                content = entry.get('content', '')
            
            # Format 2: Nested {"type": "message", "message": {"role": "...", "content": [...]}}
            elif entry.get('type') == 'message':
                msg = entry.get('message', {})
                if msg.get('role') == 'assistant':
                    msg_content = msg.get('content', [])
                    if isinstance(msg_content, list):
                        for c in msg_content:
                            if c.get('type') == 'text':
                                content += c.get('text', '') + ' '
                    else:
                        content = str(msg_content)
            
            if not content:
                continue
            
            # Extract all bead markers
            matches = BEAD_MARKER.findall(content)
            for match in matches:
                # match[0] = JSON format (old), match[1] = attribute format (new)
                raw_data = match[0] or match[1]
                try:
                    if match[0]:  # JSON format: <!--BEAD:{...}-->
                        bead_data = json.loads(raw_data)
                    else:  # Attribute format: {::bead type="..." title="..." /::}
                        bead_data = _parse_attribute_format(raw_data)
                    
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
                    
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"Line {line_num}: Failed to parse bead: {e}", file=sys.stderr)
                    continue
    
    return beads


def write_beads(beads: list[dict], session_id: str) -> int:
    """Write beads via mem-beads CLI."""
    written = 0
    
    for bead in beads:
        # Build CLI args
        args = [
            CORE_MEMORY_CLI,
            '--root', MEMBEADS_DIR,
            'add',
            '--type', bead.get('type', 'context'),
            '--title', bead.get('title', 'Untitled')[:100],  # Limit title length
            '--session-id', session_id,
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
                args.extend(['--tags'] + [str(t) for t in tags])
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


def main():
    # Get session ID from args or find latest transcript
    if len(sys.argv) >= 2:
        session_id = sys.argv[1]
        # If it's a UUID, construct path; otherwise use .mem-beads format
        if "-" in session_id and len(session_id) > 20:
            transcript = f"/home/node/.openclaw/agents/main/sessions/{session_id}.jsonl"
        else:
            transcript = f"{MEMBEADS_DIR}/session-{session_id}.jsonl"
    else:
        session_id, transcript = get_latest_transcript()
        print(f"No session ID provided, using latest: {session_id}")
        print(f"Transcript: {transcript}")
    
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
                'python3', f'{WORKSPACE}/consolidate.py',
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
