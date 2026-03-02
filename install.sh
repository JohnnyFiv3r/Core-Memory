#!/bin/bash
# mem-beads installer for OpenClaw
#
# Usage:
#   ./install.sh [--workspace PATH]
#
# Defaults to current workspace.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="${1:-$SCRIPT_DIR}"

echo "Installing mem-beads to: $WORKSPACE"

# Create directories
mkdir -p "$WORKSPACE/skills/mem-beads"
mkdir -p "$WORKSPACE/tools/mem-beads"
mkdir -p "$WORKSPACE/.mem-beads"

# Copy skill
cp "$SCRIPT_DIR/skills/mem-beads/SKILL.md" "$WORKSPACE/skills/mem-beads/"

# Copy CLI tools
cp "$SCRIPT_DIR/tools/mem-beads/mem-beads" "$WORKSPACE/tools/mem-beads/"
cp "$SCRIPT_DIR/tools/mem-beads/mem_beads.py" "$WORKSPACE/tools/mem-beads/"
cp "$SCRIPT_DIR/tools/mem-beads/consolidate.py" "$WORKSPACE/tools/mem-beads/"
cp "$SCRIPT_DIR/tools/mem-beads/associate.py" "$WORKSPACE/tools/mem-beads/"
chmod +x "$WORKSPACE/tools/mem-beads/mem-beads"

# Create sample .mem-beads structure
touch "$WORKSPACE/.mem-beads/.gitkeep"

# Add AGENTS.md snippet if not already present
AGENTS_MD="$WORKSPACE/AGENTS.md"
if [ -f "$AGENTS_MD" ]; then
    if ! grep -q "mem-beads" "$AGENTS_MD"; then
        echo '
## Mem.beads — Per-Turn Memory
After significant turns, write beads. See skills/mem-beads/SKILL.md.
' >> "$AGENTS_MD"
        echo "Added mem-beads reference to AGENTS.md"
    fi
fi

echo "✓ mem-beads installed"
echo ""
echo "Next steps:"
echo "1. Test: $WORKSPACE/tools/mem-beads/mem-beads stats"
echo "2. Configure memoryFlush in openclaw.json (optional)"
echo "3. Read: $WORKSPACE/skills/mem-beads/SKILL.md"
