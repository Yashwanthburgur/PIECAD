#!/usr/bin/env python
"""
Generator for BIP 2.3 adversarial fixtures.

Appends exactly 6 new fixtures to tests/eval_fixtures.json for box/cylinder
adversarial testing.
"""

import json
import sys
from pathlib import Path


FIXTURES_PATH = Path(__file__).resolve().parent.parent / \
    "tests" / "eval_fixtures.json"

# The 6 new BIP 2.3 fixtures
NEW_FIXTURES = [
    {
        "id": "box_negative_dim",
        "name": "Box Negative Dimension",
        "prompt": "Create a box with length -10 mm, width 20 mm, and height 10 mm.",
        "expected_tools_called": ["box"],
        "neutral_assertions": [
            {
                "type": "tool_error",
                "tool": "box",
                "expect_failure": True,
                "error_contains": "greater than 0"
            }
        ]
    },
    {
        "id": "box_vague_size",
        "name": "Box Vague Size",
        "prompt": "Make a small box.",
        "expected_tools_called": [],
        "neutral_assertions": [
            # Policy gap: no explicit behavior defined for vague dimensions
            # The agent should not fabricate dimensions without clarification
        ]
    },
    {
        "id": "box_overlapping",
        "name": "Box Overlapping Solids",
        "prompt": "Create a box 50x50x50 at origin. Then create another box 30x30x30 at the same origin.",
        "expected_tools_called": ["box", "box"],
        "expected_tool_sequence": ["box", "box"],
        "neutral_assertions": [
            # The system creates both boxes; overlap is not automatically an error
            # Interference check would be needed to detect it
            {
                "type": "tool_sequence",
                "sequence": ["box", "box"]
            }
        ]
    },
    {
        "id": "cylinder_radius_gt_height",
        "name": "Cylinder Radius Greater Than Height",
        "prompt": "Create a cylinder with radius 20 mm and height 5 mm.",
        "expected_tools_called": ["cylinder"],
        "neutral_assertions": [
            {
                "type": "tool_argument",
                "tool": "cylinder",
                "arg": "radius",
                "op": "eq",
                "value": 20.0
            },
            {
                "type": "tool_argument",
                "tool": "cylinder",
                "arg": "height",
                "op": "eq",
                "value": 5.0
            },
            "verify_exists"
        ]
    },
    {
        "id": "cylinder_vague_rod",
        "name": "Cylinder Vague Rod",
        "prompt": "Make a metal rod.",
        "expected_tools_called": [],
        "neutral_assertions": [
            # Policy gap: no explicit behavior defined for vague dimensions
            # The agent should not fabricate dimensions without clarification
        ]
    },
    {
        "id": "cylinder_negative_radius",
        "name": "Cylinder Negative Radius",
        "prompt": "Create a cylinder with radius -5 mm and height 20 mm.",
        "expected_tools_called": ["cylinder"],
        "neutral_assertions": [
            {
                "type": "tool_error",
                "tool": "cylinder",
                "expect_failure": True,
                "error_contains": "greater than 0"
            }
        ]
    }
]


def main():
    # Load existing fixtures
    with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
        existing = json.load(f)

    # Verify existing fixture count
    existing_ids = {f.get("id") or f.get("name") for f in existing}
    print(f"Existing fixtures: {len(existing)}")
    for fid in sorted(existing_ids):
        print(f"  - {fid}")

    # Check for duplicates
    new_ids = {f["id"] for f in NEW_FIXTURES}
    duplicates = new_ids & existing_ids
    if duplicates:
        print(f"ERROR: Duplicate fixture IDs found: {duplicates}")
        print("Refusing to append duplicates.")
        sys.exit(1)

    # Verify expected count
    if len(existing) != 14:
        print(f"WARNING: Expected 14 existing fixtures, found {len(existing)}")
        print("Continuing anyway...")

    # Append new fixtures
    combined = existing + NEW_FIXTURES

    # Write back
    with open(FIXTURES_PATH, "w", encoding="utf-8") as f:
        json.dump(combined, f, indent=2)
        f.write("\n")  # trailing newline

    print(f"\nAppended {len(NEW_FIXTURES)} new fixtures.")
    print(f"Total fixtures: {len(combined)}")

    # Verify
    with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
        verify = json.load(f)
    verify_ids = {f.get("id") or f.get("name") for f in verify}
    for fid in sorted(verify_ids):
        print(f"  - {fid}")

    if len(verify) != 20:
        print(f"ERROR: Expected 20 total fixtures, got {len(verify)}")
        sys.exit(1)

    print("\nValidation passed!")


if __name__ == "__main__":
    main()
