#!/bin/sh
# Run mem-beads association crawler via sub-agent
#
# Usage:
#   MEMBEADS_ASSOCIATION_MODEL=minimax/MiniMax-M2.5 ./run_association.sh
#
# Model defaults to minimax/MiniMax-M2.5 if not set.
# The sub-agent will analyze beads and record associations.

SCRIPT_DIR="$(dirname "$0")"
MODEL="${MEMBEADS_ASSOCIATION_MODEL:-minimax/MiniMax-M2.5}"

echo "=== Generating analysis prompt ==="
PROMPT=$(python3 "$SCRIPT_DIR/associate.py" prompt --json 2>&1)
if [ $? -ne 0 ]; then
    echo "Failed to generate prompt: $PROMPT"
    exit 1
fi

# Extract prompt from JSON
PROMPT_TEXT=$(echo "$PROMPT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('prompt',''))")

if [ -z "$PROMPT_TEXT" ]; then
    echo "No beads to analyze"
    exit 0
fi

TASK="You are a memory analysis agent. Analyze these memory beads and identify semantic associations.

$PROMPT_TEXT

Output ONLY a JSON array of associations (no markdown). Format:
[{\"source\": \"bead-XXX\", \"target\": \"bead-YYY\", \"relationship\": \"similar_pattern\", \"explanation\": \"...\", \"confidence\": 0.8}]

If none, output: []"

# The sessions_spawn tool will be called by the main agent
# This script outputs the task for manual or scripted execution
echo "=== Task for sub-agent (model: $MODEL) ==="
echo "$TASK"
echo ""
echo "To run automatically, have the main agent call:"
echo "  sessions_spawn with model='$MODEL' and task='<above>'"
echo "Then record results with:"
echo "  python3 associate.py record --associations '<json>'"
