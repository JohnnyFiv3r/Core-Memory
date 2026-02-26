#!/usr/bin/env python3
"""
Generate a sub-agent prompt for per-turn bead analysis.
Called with turn context, outputs the prompt to stdout.

Usage:
  python3 turn_prompt.py --session <id> --user "msg" --agent "response" [--tools "summary"]
"""

import argparse
import json
import sys

PROMPT_TEMPLATE = """Analyze this agent turn and decide if it warrants a memory bead.

## Turn Context
**User said:** {user_msg}

**Agent responded:** {agent_msg}

**Tools used:** {tools}

## Session
Session ID: {session_id}

## Instructions
1. Decide: does this turn contain something worth remembering in future sessions?
2. If NO — reply with exactly: NO_BEAD
3. If YES — create a bead using the CLI, then reply with the bead ID.

### Bead-Worthy Criteria
- A goal, decision, lesson, outcome, evidence, precedent, or important context
- Something that would help the agent in a future session
- NOT: casual chat, greetings, simple Q&A, continuations of already-beaded work

### How to Create
```bash
/home/node/.openclaw/workspace/tools/mem-beads/mem-beads create \\
  --type <goal|decision|tool_call|evidence|outcome|lesson|precedent|context> \\
  --title "Short specific title (5-15 words)" \\
  --summary "Key point 1" "Key point 2" \\
  --session {session_id} \\
  --scope <personal|project|global> \\
  --tags "relevant,tags" \\
  --confidence <0.5-1.0>
```

### Rules
- ONE bead per turn maximum
- Title must be specific and searchable
- Summary: 1-3 bullet points, each stands alone
- Confidence: 0.9+ if user-confirmed, 0.7-0.9 if agent-inferred
- Tags: lowercase, include project name if project-scoped
- DO NOT create session_start or session_end beads
"""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--tools", default="none")
    parser.add_argument("--json", action="store_true", help="Output as JSON for sessions_spawn")
    args = parser.parse_args()

    prompt = PROMPT_TEMPLATE.format(
        user_msg=args.user[:2000],  # truncate to control token usage
        agent_msg=args.agent[:2000],
        tools=args.tools[:500],
        session_id=args.session,
    )

    if args.json:
        print(json.dumps({"task": prompt, "model": "minimax-fast"}))
    else:
        print(prompt)

if __name__ == "__main__":
    main()
