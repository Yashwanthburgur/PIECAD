#!/usr/bin/env python
"""
Test script: Edit/Resize Hole Test
Verifies the LLM can use the hierarchical DAG state to identify the underlying
tool of a boolean cut and edit its properties, rather than spawning a new object.
"""
from core.adapters.interfaces import CADAdapter
from core.agent import CADAgent
import json
import importlib
import os

# Setup paths and imports
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")


# Dynamically load the adapter class (same as core/api.py)
ACTIVE_CAD_ADAPTER = os.getenv("ACTIVE_CAD_ADAPTER", "freecad")
ADAPTER_FACTORY = {
    "freecad": "adapters.freecad.adapter.FreeCADAdapter"
}

module_path, class_name = ADAPTER_FACTORY[ACTIVE_CAD_ADAPTER].rsplit(".", 1)
_mod = importlib.import_module(module_path)
_AdapterClass = getattr(_mod, class_name)


def main():
    print("=" * 60)
    print("TEST: Edit/Resize Hole Test")
    print("=" * 60)

    # Instantiate adapter and agent
    print("\n[Setup] Creating FreeCADAdapter (port=9876)...")
    adapter = _AdapterClass(port=9876)

    print("[Setup] Creating CADAgent...")
    agent = CADAgent(adapter=adapter)

    # Prompt 1: Create base plate + cylinder + boolean cut
    prompt1 = (
        "Make a base plate box 50x50x5 at origin 0,0,0. "
        "Make a cylinder radius 5, height 20 at origin 25,25,0. "
        "Subtract the cylinder from the box to make a hole."
    )

    print("\n" + "=" * 60)
    print("PROMPT 1:")
    print(prompt1)
    print("=" * 60)

    response1 = agent.handle_message(prompt1)
    print(f"\n[Agent Response 1]: {response1}")

    # Prompt 2: Edit the hole radius
    prompt2 = "Change the radius of that hole to 12."

    print("\n" + "=" * 60)
    print("PROMPT 2:")
    print(prompt2)
    print("=" * 60)

    response2 = agent.handle_message(prompt2)
    print(f"\n[Agent Response 2]: {response2}")

    # Fetch final state
    print("\n" + "=" * 60)
    print("FINAL STATE (from adapter.get_state()):")
    print("=" * 60)

    final_state_json = adapter.get_state()
    try:
        final_state = json.loads(final_state_json)
        print(json.dumps(final_state, indent=2))
    except json.JSONDecodeError:
        print(f"Raw state: {final_state_json}")

    # Verification
    print("\n" + "=" * 60)
    print("VERIFICATION:")
    print("=" * 60)

    if isinstance(final_state, list):
        # Look for cylinder objects
        cylinders = [obj for obj in final_state if "cylinder" in obj.get(
            "type", "").lower() or obj.get("label", "").lower().startswith("cylinder")]
        cut_objects = [obj for obj in final_state if obj.get(
            "type", "") == "Part::Cut"]

        print(f"Found {len(cylinders)} cylinder(s)")
        print(f"Found {len(cut_objects)} Cut object(s)")

        for cyl in cylinders:
            props = cyl.get("properties", {})
            radius = props.get("Radius", "N/A")
            print(
                f"  Cylinder '{cyl['id']}' (label: {cyl['label']}): Radius = {radius}")

        for cut in cut_objects:
            print(
                f"  Cut '{cut['id']}': children={cut.get('children', [])}, parents={cut.get('parents', [])}")

        # Check if any cylinder has radius 12.0
        radius_12_found = any(
            cyl.get("properties", {}).get("Radius") == 12.0
            for cyl in cylinders
        )

        if radius_12_found:
            print("\n✅ SUCCESS: Cylinder radius updated to 12.0!")
        else:
            print("\n❌ FAILURE: No cylinder with radius 12.0 found")

        # Check for duplicates (more than 1 cylinder with same base properties)
        if len(cylinders) > 1:
            print(
                f"\n⚠️  WARNING: {len(cylinders)} cylinders found (possible duplicate creation)")
        else:
            print(f"\n✅ No duplicate cylinders spawned")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
