#!/usr/bin/env python3
"""
mem-beads setup / Dreamer onboarding

Run this to configure:
1. Dreamer (association crawler) preferences
2. MemoryFlush config in openclaw.json
3. Bead instructions in AGENTS.md
"""

import json
import os
import re
import sys

# Detect workspace - look for openclaw.json in common locations
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def find_workspace():
    """Find the workspace by looking for openclaw.json and AGENTS.md."""
    candidates = []
    
    # Add explicit paths
    if os.environ.get("OPENCLAW_WORKSPACE"):
        candidates.append(os.environ["OPENCLAW_WORKSPACE"])
    
    # Common patterns: tools/mem-beads -> workspace -> parent
    if SCRIPT_DIR.endswith("/tools/mem-beads"):
        candidates.append(SCRIPT_DIR.replace("/tools/mem-beads", ""))
        candidates.append(os.path.dirname(SCRIPT_DIR.replace("/tools/mem-beads", "")))
    
    # Standard workspace location
    candidates.append(os.path.expanduser("~/.openclaw/workspace"))
    
    # Current directory
    candidates.append(os.getcwd())
    candidates.append(os.path.dirname(os.getcwd()))
    
    # Check each candidate for AGENTS.md or openclaw.json
    for path in candidates:
        if not path:
            continue
        if os.path.exists(os.path.join(path, "AGENTS.md")):
            return path
        if os.path.exists(os.path.join(path, "openclaw.json")):
            # This is the gateway dir, workspace is likely a subdir
            workspace_candidate = os.path.join(path, "workspace")
            if os.path.exists(workspace_candidate):
                return workspace_candidate
    
    # Fallback
    return os.getcwd()

WORKSPACE = find_workspace()

# openclaw.json is typically in the gateway dir (parent of workspace)
# Try workspace first, then parent
OPENCLAW_JSON = os.path.join(WORKSPACE, "openclaw.json")
if not os.path.exists(OPENCLAW_JSON):
    # Maybe we're in the gateway dir, not workspace
    alt_path = os.path.join(os.path.dirname(WORKSPACE), "openclaw.json")
    if os.path.exists(alt_path):
        OPENCLAW_JSON = alt_path

CONFIG_FILE = os.path.join(WORKSPACE, ".mem-beads", "config.json")
AGENTS_MD = os.path.join(WORKSPACE, "AGENTS.md")

DEFAULT_CONFIG = {
    "timezone": "America/Chicago",
    "interval": "12h",
    "cron_schedule": "0 14,2 * *",
    "custom_notes": "",
    "first_run": True,
    "onboarded_at": None,
}

MEMORYFLUSH_CONFIG = {
    "enabled": True,
    "prompt": "Run bead extraction FIRST, then consolidation: python3 {tools_dir}/extract-beads.py && python3 {tools_dir}/consolidate.py consolidate --session main --promote. Reply with NO_REPLY."
}

BEAD_INSTRUCTIONS = """
### 📿 Bead Writing (Every Turn)

This agent uses **mem-beads** for structured memory. After EVERY response, write a bead:

```
<!--BEAD:{{"type":"<type>","title":"<short title>","summary":["<bullet>"],"session":"<session-id>","turn":<turn-number>}-->
```

Write a bead EVERY turn. The post-session extractor parses these markers.

#### Bead Types (use exactly these):

| Type | Purpose | Example |
|------|---------|---------|
| goal | User or agent intent | "User wants to configure memoryFlush" |
| decision | Choice made with rationale | "Decided to use marker format" |
| tool_call | External action taken | "Called mem-beads CLI to create bead" |
| evidence | Data supporting a decision | "Config schema shows memoryFlush path" |
| outcome | Result of a goal/decision | "Bead created successfully" |
| lesson | Insight derived from outcome | "No per-turn hooks in OpenClaw" |
| context | Useful context for future turns | "Session using MiniMax M2.5 model" |
| checkpoint | Intermediate state snapshot | "Feature built successfully" |
| precedent | Historical pattern/rule discovered | "Graphiti as next layer" |
| failed_hypothesis | Assumption that proved wrong | "Sub-agents would work per-turn" |
| reversal | Earlier decision overturned | "Changed from event-sourced to index-first" |
| misjudgment | Incorrect assessment made | "Thought config was in wrong place" |
| overfitted_pattern | Pattern that won't generalize | "Same solution won't work again" |
| abandoned_path | Approach tried and dropped | "Tried cron, used memoryFlush instead" |
| reflection | Thinking about the process | "Bead system needs automatic extraction" |
| design_principle | Architectural rule | "Reduce entropy before adding intelligence" |

#### Minimal Context Bead (routine turns):

```
<!--BEAD:{{"type":"context","title":"Routine turn","summary":["Standard query"],"session":"main","turn":1}-->
```

#### Full Example:

```
<!--BEAD:{{"type":"lesson","title":"No per-turn hooks in OpenClaw","summary":["Discovered OpenClaw lacks automatic per-turn sub-agent spawning","memoryFlush is the only pre-compaction hook","Model-written beads with post-run extraction solves this"],"session":"main","turn":42,"scope":"project"}}-->
```

**Note:** Beads are extracted post-session via memoryFlush. Don't write beads directly to CLI — use markers.

"""


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def configure_openclaw_json():
    """Add memoryFlush config to openclaw.json."""
    if not os.path.exists(OPENCLAW_JSON):
        print(f"⚠️  openclaw.json not found at {OPENCLAW_JSON}")
        print("   Skipping memoryFlush config.")
        return False
    
    with open(OPENCLAW_JSON, "r") as f:
        config = json.load(f)
    
    # Navigate to agents.defaults.compaction
    if "agents" not in config:
        config["agents"] = {}
    if "defaults" not in config["agents"]:
        config["agents"]["defaults"] = {}
    if "compaction" not in config["agents"]["defaults"]:
        config["agents"]["defaults"]["compaction"] = {}
    
    # Check if memoryFlush already configured
    if config["agents"]["defaults"]["compaction"].get("memoryFlush", {}).get("enabled"):
        print("✓ memoryFlush already configured in openclaw.json")
        return True
    
    # Determine tools path
    tools_dir = os.path.join(WORKSPACE, "tools", "mem-beads")
    if not os.path.exists(tools_dir):
        tools_dir = SCRIPT_DIR
    
    # Add memoryFlush config
    memoryflush = MEMORYFLUSH_CONFIG.copy()
    memoryflush["prompt"] = memoryflush["prompt"].format(tools_dir=tools_dir)
    config["agents"]["defaults"]["compaction"]["memoryFlush"] = memoryflush
    
    with open(OPENCLAW_JSON, "w") as f:
        json.dump(config, f, indent=2)
    
    print(f"✓ Added memoryFlush config to openclaw.json")
    return True


def configure_agents_md():
    """Add bead instructions to AGENTS.md."""
    if not os.path.exists(AGENTS_MD):
        print(f"⚠️  AGENTS.md not found at {AGENTS_MD}")
        print("   Skipping bead instructions.")
        return False
    
    with open(AGENTS_MD, "r") as f:
        content = f.read()
    
    # Check if already configured
    if "Bead Writing (Every Turn)" in content:
        print("✓ Bead instructions already in AGENTS.md")
        return True
    
    # Find a good insertion point - after "Memory" section
    memory_section_match = re.search(r'(## Memory\n.*?)(## |\Z)', content, re.DOTALL)
    
    if memory_section_match:
        insert_pos = memory_section_match.end()
        new_content = content[:insert_pos] + BEAD_INSTRUCTIONS + content[insert_pos:]
    else:
        # Just append at end
        new_content = content + BEAD_INSTRUCTIONS
    
    with open(AGENTS_MD, "w") as f:
        f.write(new_content)
    
    print(f"✓ Added bead instructions to AGENTS.md")
    return True


def onboarding():
    print("=" * 60)
    print("mem-beads v2.0 Setup - Automatic Bead Extraction")
    print("=" * 60)
    print()
    print("This will configure:")
    print("  1. Dreamer preferences (timezone, interval)")
    print("  2. MemoryFlush config in openclaw.json")
    print("  3. Bead instructions in AGENTS.md")
    print()
    
    # Check workspace
    if not os.path.exists(WORKSPACE):
        print(f"⚠️  Workspace not found: {WORKSPACE}")
        print("   Set OPENCLAW_WORKSPACE env var.")
        return
    
    print(f"Workspace: {WORKSPACE}")
    print()
    
    # Configure OpenClaw
    print("--- OpenClaw Configuration ---")
    openclaw_configured = configure_openclaw_json()
    print()
    
    # Configure AGENTS.md
    print("--- AGENTS.md Configuration ---")
    agents_configured = configure_agents_md()
    print()
    
    # Dreamer preferences
    print("--- Dreamer Preferences ---")
    cfg = load_config()
    
    print(f"1. Timezone: [{cfg['timezone']}]")
    tz = input("   Press Enter for default, or enter your timezone: ").strip()
    if tz:
        cfg["timezone"] = tz
    print()
    
    print(f"2. Dream interval: [{cfg['interval']}] (how often association analysis runs)")
    print("   Options: 6h, 12h, 24h")
    interval = input("   Press Enter for default (12h): ").strip()
    if interval:
        cfg["interval"] = interval
    print()
    
    print(f"3. Custom notes: [{cfg.get('custom_notes', '')}]")
    print("   Things you want Dreamer to look for?")
    notes = input("   Press Enter to skip: ").strip()
    if notes:
        cfg["custom_notes"] = notes
    print()
    
    # Summary
    print("=" * 60)
    print("Configuration Summary:")
    print(f"  Workspace: {WORKSPACE}")
    print(f"  Timezone: {cfg['timezone']}")
    print(f"  Interval: {cfg['interval']}")
    print(f"  Notes: {cfg.get('custom_notes', '(none)')}")
    print("=" * 60)
    
    confirm = input("\nSave configuration? (y/n): ").strip().lower()
    if confirm == "y":
        cfg["first_run"] = False
        from datetime import datetime, timezone
        cfg["onboarded_at"] = datetime.now(timezone.utc).isoformat()
        save_config(cfg)
        print("\n✓ Configuration saved!")
        print()
        print("Next steps:")
        print("  1. Restart OpenClaw gateway: openclaw gateway restart")
        print("  2. Start chatting - beads will be written automatically")
    else:
        print("\nConfiguration not saved.")


def get_prompt_suffix():
    """Get custom notes to append to the analysis prompt."""
    cfg = load_config()
    if cfg.get("custom_notes"):
        return f"\n\nUSER PREFERENCES (from setup):\n{cfg['custom_notes']}"
    return ""


if __name__ == "__main__":
    if "--prompt-suffix" in sys.argv:
        print(get_prompt_suffix())
    else:
        onboarding()
