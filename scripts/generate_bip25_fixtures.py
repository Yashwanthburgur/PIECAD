#!/usr/bin/env python
"""
Generator for BIP 2.5 adversarial fixtures (hole & sketch operations).

Appends exactly 6 new fixtures to tests/eval_fixtures.json.
"""

import json
import sys
from pathlib import Path


FIXTURES_PATH = Path(__file__).resolve().parent.parent / \
    "tests" / "eval_fixtures.json"

# The 6 new BIP 2.5 fixtures
NEW_FIXTURES = [
    {
        "id": "hole_diameter_too_large",
        "name": "Hole Diameter Too Large",
        "prompt": "Create a box 20x20x20. Get its top face. Drill a simple hole through it with diameter 30 mm at the center.",
        "expected_tools_called": ["box", "get_faces", "hole"],
        "expected_tool_sequence": ["box", "get_faces", "hole"],
        "neutral_assertions": [
            {
                "type": "tool_sequence",
                "sequence": ["box", "get_faces", "hole"]
            },
            {
                "type": "tool_argument",
                "tool": "hole",
                "arg": "diameter",
                "op": "eq",
                "value": 30.0
            }
            # The implementation may fail at CAD kernel level (diameter > target face)
            # but schema validation passes (diameter > 0). No tool_error assertion since
            # we cannot guarantee the exact failure behavior without live CAD.
        ]
    },
    {
        "id": "hole_bad_thread",
        "name": "Hole Invalid Thread Spec",
        "prompt": "Create a box 20x20x20. Get its top face. Drill a tapped hole with thread_spec 'NOT_A_REAL_THREAD_SPEC' at the center.",
        "expected_tools_called": ["box", "get_faces", "hole"],
        "expected_tool_sequence": ["box", "get_faces", "hole"],
        "neutral_assertions": [
            {
                "type": "tool_sequence",
                "sequence": ["box", "get_faces", "hole"]
            },
            {
                "type": "tool_argument",
                "tool": "hole",
                "arg": "kind",
                "op": "eq",
                "value": "tapped"
            },
            {
                "type": "tool_argument",
                "tool": "hole",
                "arg": "thread_spec",
                "op": "eq",
                "value": "NOT_A_REAL_THREAD_SPEC"
            }
            # The schema accepts any string for thread_spec (Optional[str]),
            # so validation passes. CAD kernel may or may not reject it.
        ]
    },
    {
        "id": "hole_out_of_bounds",
        "name": "Hole Out of Bounds",
        "prompt": "Create a box 20x20x20. Get its top face. Drill a simple hole with diameter 5 mm positioned at x=100, y=100 (far outside the face).",
        "expected_tools_called": ["box", "get_faces", "hole"],
        "expected_tool_sequence": ["box", "get_faces", "hole"],
        "neutral_assertions": [
            {
                "type": "tool_sequence",
                "sequence": ["box", "get_faces", "hole"]
            },
            {
                "type": "tool_argument",
                "tool": "hole",
                "arg": "diameter",
                "op": "eq",
                "value": 5.0
            },
            {
                "type": "tool_argument",
                "tool": "hole",
                "arg": "origin",
                "op": "eq",
                "value": {"x": 100.0, "y": 100.0, "z": 0.0}
            }
            # The adapter passes origin through to CAD kernel.
            # Whether out-of-bounds is rejected depends on CAD kernel behavior.
        ]
    },
    {
        "id": "sketch_self_intersecting",
        "name": "Sketch Self Intersecting",
        "prompt": "Create a box 50x50x10. Get its top face. Create a sketch on that face with two overlapping circles that cross each other: one at x=-10 radius 15, another at x=10 radius 15.",
        "expected_tools_called": ["box", "get_faces", "sketch"],
        "expected_tool_sequence": ["box", "get_faces", "sketch"],
        "neutral_assertions": [
            {
                "type": "tool_sequence",
                "sequence": ["box", "get_faces", "sketch"]
            },
            {
                "type": "tool_argument",
                "tool": "sketch",
                "arg": "shapes",
                "op": "eq",
                "value": [
                    {"type": "circle", "x": -10.0, "y": 0.0, "radius": 15.0},
                    {"type": "circle", "x": 10.0, "y": 0.0, "radius": 15.0}
                ]
            }
            # The bridge creates a compound of the two circles.
            # If they self-intersect, FreeCAD may reject the sketch.
            # We verify the shapes were passed as requested.
        ]
    },
    {
        "id": "sketch_zero_area",
        "name": "Sketch Zero Area",
        "prompt": "Create a box 50x50x10. Get its top face. Create a sketch on that face with a degenerate rectangle of width 0.001 and height 0.001 (near-zero area).",
        "expected_tools_called": ["box", "get_faces", "sketch"],
        "expected_tool_sequence": ["box", "get_faces", "sketch"],
        "neutral_assertions": [
            {
                "type": "tool_sequence",
                "sequence": ["box", "get_faces", "sketch"]
            },
            {
                "type": "tool_argument",
                "tool": "sketch",
                "arg": "shapes",
                "op": "eq",
                "value": [
                    {"type": "rectangle", "x": 0.0, "y": 0.0,
                        "width": 0.001, "height": 0.001}
                ]
            }
            # Schema requires width > 0, height > 0, so 0.001 is valid but near-zero.
            # Whether CAD kernel rejects near-zero area is implementation-dependent.
        ]
    },
    {
        "id": "sketch_bad_face",
        "name": "Sketch Bad Face Reference",
        "prompt": "Create a box 50x50x10. Attempt to create a sketch on face 'NonExistent_face_99' with a simple 10x10 rectangle at the center.",
        "expected_tools_called": ["box", "sketch"],
        "expected_tool_sequence": ["box", "sketch"],
        "neutral_assertions": [
            {
                "type": "tool_sequence",
                "sequence": ["box", "sketch"]
            },
            {
                "type": "tool_argument",
                "tool": "sketch",
                "arg": "face_ref",
                "op": "eq",
                "value": "NonExistent_face_99"
            }
            # The bridge validates face_ref format and target object existence.
            # A nonexistent object should produce a structured error.
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

    # Check for duplicates
    new_ids = {f["id"] for f in NEW_FIXTURES}
    duplicates = new_ids & existing_ids
    if duplicates:
        print(f"ERROR: Duplicate fixture IDs found: {duplicates}")
        print("Refusing to append duplicates.")
        sys.exit(1)

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

    if len(verify) != len(existing) + 6:
        print(
            f"ERROR: Expected {len(existing) + 6} total fixtures, got {len(verify)}")
        sys.exit(1)

    print("\nValidation passed!")


if __name__ == "__main__":
    main()
