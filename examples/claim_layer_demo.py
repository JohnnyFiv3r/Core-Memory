#!/usr/bin/env python3
"""
Claim Layer Demo — shows write, update, and resolution flow.

Contract Level: Experimental
Audience: Contributors and integrators exploring the claim layer

This example demonstrates:
1. Writing claims to beads
2. Emitting claim updates when facts change
3. Resolving current claim state
4. Viewing full store state

Run:
  cd /Users/johninniger/Documents/Playground/Core-Memory
  python examples/claim_layer_demo.py

Or:
  PYTHONPATH=. python examples/claim_layer_demo.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core_memory.persistence.store_claim_ops import (
    write_claims_to_bead,
    write_claim_updates_to_bead,
    resolve_current_state,
)
from core_memory.claim.resolver import resolve_all_current_state


def main():
    """Run the claim layer demo."""
    with tempfile.TemporaryDirectory() as root:
        print("=" * 60)
        print("CLAIM LAYER DEMO")
        print("=" * 60)
        print()

        # ================================================================
        # Step 1: Write initial claim to bead_001
        # ================================================================
        print("STEP 1: Write initial claim (user prefers tea)")
        print("-" * 60)

        claim1 = {
            "id": "c_beverage_1",
            "claim_kind": "preference",
            "subject": "user",
            "slot": "beverage",
            "value": "tea",
            "reason_text": "User said 'I prefer tea in the morning'",
            "confidence": 0.85,
        }

        write_claims_to_bead(root, "bead_001", [claim1])
        print(f"✓ Wrote claim c_beverage_1 to bead_001")
        print(f"  Subject: {claim1['subject']}")
        print(f"  Slot: {claim1['slot']}")
        print(f"  Value: {claim1['value']}")
        print(f"  Confidence: {claim1['confidence']}")
        print()

        # ================================================================
        # Step 2: Resolve current state after first claim
        # ================================================================
        print("STEP 2: Resolve current state (before update)")
        print("-" * 60)

        state = resolve_current_state(root, "user", "beverage")
        current = state["current_claim"]
        print(f"✓ Resolved user:beverage")
        print(f"  Status: {state['status']}")
        print(f"  Current value: {current['value'] if current else 'None'}")
        print(f"  Confidence: {current['confidence'] if current else 'N/A'}")
        print(f"  History length: {len(state['history'])}")
        print()

        # ================================================================
        # Step 3: Write new claim to bead_002 (user preference changed)
        # ================================================================
        print("STEP 3: Write new claim (user now prefers coffee)")
        print("-" * 60)

        claim2 = {
            "id": "c_beverage_2",
            "claim_kind": "preference",
            "subject": "user",
            "slot": "beverage",
            "value": "coffee",
            "reason_text": "User said 'Actually I switched to coffee'",
            "confidence": 0.90,
        }

        write_claims_to_bead(root, "bead_002", [claim2])
        print(f"✓ Wrote claim c_beverage_2 to bead_002")
        print(f"  Subject: {claim2['subject']}")
        print(f"  Slot: {claim2['slot']}")
        print(f"  Value: {claim2['value']}")
        print(f"  Confidence: {claim2['confidence']}")
        print()

        # ================================================================
        # Step 4: Emit supersede update
        # ================================================================
        print("STEP 4: Emit supersede update (c_beverage_1 → c_beverage_2)")
        print("-" * 60)

        update = {
            "id": f"u_{uuid.uuid4().hex[:8]}",
            "decision": "supersede",
            "target_claim_id": "c_beverage_1",
            "replacement_claim_id": "c_beverage_2",
            "subject": "user",
            "slot": "beverage",
            "reason_text": "User preference changed in bead_002",
            "trigger_bead_id": "bead_002",
            "confidence": 0.90,
        }

        write_claim_updates_to_bead(root, "bead_002", [update])
        print(f"✓ Emitted update {update['id']}")
        print(f"  Decision: supersede")
        print(f"  Target: c_beverage_1 → superseded by c_beverage_2")
        print(f"  Trigger bead: bead_002")
        print()

        # ================================================================
        # Step 5: Resolve current state after update
        # ================================================================
        print("STEP 5: Resolve current state (after supersede update)")
        print("-" * 60)

        state = resolve_current_state(root, "user", "beverage")
        current = state["current_claim"]
        print(f"✓ Resolved user:beverage")
        print(f"  Status: {state['status']}")
        print(f"  Current value: {current['value'] if current else 'None'}")
        print(f"  Confidence: {current['confidence'] if current else 'N/A'}")
        print(f"  History length: {len(state['history'])}")
        print(f"  Conflicts: {len(state['conflicts'])}")
        print()

        # ================================================================
        # Step 6: Add another claim (user timezone)
        # ================================================================
        print("STEP 6: Write claim about user timezone")
        print("-" * 60)

        claim3 = {
            "id": "c_timezone_1",
            "claim_kind": "condition",
            "subject": "user",
            "slot": "timezone",
            "value": "UTC-8",
            "reason_text": "User profile indicates Pacific timezone",
            "confidence": 0.95,
        }

        write_claims_to_bead(root, "bead_003", [claim3])
        print(f"✓ Wrote claim c_timezone_1 to bead_003")
        print(f"  Subject: {claim3['subject']}")
        print(f"  Slot: {claim3['slot']}")
        print(f"  Value: {claim3['value']}")
        print()

        # ================================================================
        # Step 7: Resolve all current state in store
        # ================================================================
        print("STEP 7: Resolve full store state (all subject:slot pairs)")
        print("-" * 60)

        full = resolve_all_current_state(root)
        print(f"✓ Resolved full store")
        print(f"  Total slots: {full['total_slots']}")
        print(f"  Active slots: {full['active_slots']}")
        print(f"  Conflicted slots: {full['conflict_slots']}")
        print()

        print("  Slot inventory:")
        for slot_key in sorted(full["slots"].keys()):
            slot_data = full["slots"][slot_key]
            current_claim = slot_data["current_claim"]
            status = slot_data["status"]
            value = current_claim["value"] if current_claim else "None"
            print(f"    {slot_key}:")
            print(f"      Current: {value}")
            print(f"      Status: {status}")
            print(f"      History: {len(slot_data['history'])} claim(s)")
        print()

        # ================================================================
        # Step 8: Show detailed timeline for beverage slot
        # ================================================================
        print("STEP 8: Detailed timeline for user:beverage")
        print("-" * 60)

        full = resolve_all_current_state(root)
        beverage_slot = full["slots"].get("user:beverage", {})
        timeline = beverage_slot.get("timeline", [])

        print(f"✓ Timeline has {len(timeline)} events:")
        for i, event in enumerate(timeline, start=1):
            event_type = event.get("event_type", "unknown")
            claim = event.get("claim", {})
            update = event.get("update", {})

            if event_type == "assert":
                print(f"  {i}. ASSERT claim {claim.get('id')}")
                print(f"     Value: {claim.get('value')}")
                print(f"     Confidence: {claim.get('confidence')}")
            else:
                print(f"  {i}. {event_type.upper()} update (target: {update.get('target_claim_id')})")
                if update.get("replacement_claim_id"):
                    print(f"     Replacement: {update.get('replacement_claim_id')}")
        print()

        # ================================================================
        # Summary
        # ================================================================
        print("=" * 60)
        print("DEMO COMPLETE")
        print("=" * 60)
        print()
        print("Summary:")
        print(f"  Store root: {root}")
        print(f"  Total claims: {sum(len(s['history']) for s in full['slots'].values())}")
        print(f"  Total slots: {full['total_slots']}")
        print(f"  Active slots: {full['active_slots']}")
        print()
        print("Key takeaways:")
        print("  1. Claims are immutable; changes use updates not mutations")
        print("  2. resolve_current_state() finds the active claim for subject+slot")
        print("  3. resolve_all_current_state() maps all slots in the store")
        print("  4. History is always preserved; status codes track state changes")
        print("  5. Supersede updates link old and new claims")
        print()


if __name__ == "__main__":
    main()
