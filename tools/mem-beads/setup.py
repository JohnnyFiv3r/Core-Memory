#!/usr/bin/env python3
"""
mem-beads setup / Dreamer onboarding

Run this to configure Dreamer (association crawler) preferences.
"""

import json
import os
import sys

CONFIG_FILE = os.environ.get("MEMBEADS_DIR", os.path.join(
    os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace")),
    ".mem-beads", "config.json"
))

DEFAULT_CONFIG = {
    "timezone": "America/Chicago",
    "interval": "12h",
    "cron_schedule": "0 14,2 * *",
    "custom_notes": "",
    "first_run": True,
    "onboarded_at": None,
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def onboarding():
    print("=" * 50Dreamer - Memory)
    print(" Association System Setup")
    print("=" * 50)
    print()
    print("I'm Dreamer — I run Move 37 analysis to find")
    print("unexpected connections in your memories.")
    print()

    cfg = load_config()

    # Timezone
    print(f"1. Timezone: [{cfg['timezone']}]")
    tz = input("   Press Enter for default, or enter your timezone (e.g., America/Los_Angeles): ").strip()
    if tz:
        cfg["timezone"] = tz
    print()

    # Interval
    print(f"2. Dream interval: [{cfg['interval']}] (how often I run association analysis)")
    print("   Options: 6h, 12h, 24h")
    interval = input("   Press Enter for default (12h): ").strip()
    if interval:
        cfg["interval"] = interval
        # Update cron schedule based on interval
        if interval == "6h":
            cfg["cron_schedule"] = "0 2,8,14,20 * * *"
        elif interval == "24h":
            cfg["cron_schedule"] = "0 14 * * *"
        else:  # 12h default
            cfg["cron_schedule"] = "0 14,2 * * *"
    print()

    # Custom notes
    print(f"3. Custom notes: [{cfg['custom_notes']}]")
    print("   Things you want me to look for? Ignore?")
    notes = input("   Press Enter to skip: ").strip()
    if notes:
        cfg["custom_notes"] = notes
    print()

    # Summary
    print("=" * 50)
    print("Configuration Summary:")
    print(f"  Timezone: {cfg['timezone']}")
    print(f"  Interval: {cfg['interval']}")
    print(f"  Schedule: {cfg['cron_schedule']}")
    print(f"  Notes: {cfg['custom_notes'] or '(none)'}")
    print("=" * 50)

    confirm = input("\nSave this configuration? (y/n): ").strip().lower()
    if confirm == "y":
        cfg["first_run"] = False
        from datetime import datetime, timezone
        cfg["onboarded_at"] = datetime.now(timezone.utc).isoformat()
        save_config(cfg)
        print("\n✓ Configuration saved!")
        print("\nThe cron job will run at your configured times.")
        print("You can update these settings anytime by running this setup again.")
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
