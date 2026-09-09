#!/usr/bin/env python
"""
PieCAD Evaluation Runner - Headless Evaluation Harness
Executes test fixtures against the PieCAD agent and produces a Pass/Fail scoreboard.
"""
from core.adapters.interfaces import CADAdapter
from core.agent import CADAgent
import json
import sys
import os
import importlib
from typing import Dict, List, Any, Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_adapter() -> CADAdapter:
    """Load the FreeCAD adapter using the same factory pattern as core/api.py."""
    ACTIVE_CAD_ADAPTER = os.getenv("ACTIVE_CAD_ADAPTER", "freecad")
    ADAPTER_FACTORY = {
        "freecad": "adapters.freecad.adapter.FreeCADAdapter"
    }

    module_path, class_name = ADAPTER_FACTORY[ACTIVE_CAD_ADAPTER].rsplit(
        ".", 1)
    _mod = importlib.import_module(module_path)
    _AdapterClass = getattr(_mod, class_name)

    # Port 9876 is the default bridge port
    adapter = _AdapterClass(port=9876)
    return adapter


def load_fixtures(path: str) -> List[Dict[str, Any]]:
    """Load test fixtures from JSON file."""
    with open(path, "r") as f:
        return json.load(f)


def check_condition(actual_state: List[Dict[str, Any]], condition: Dict[str, Any]) -> tuple[bool, str]:
    """
    Check if a single expected condition is satisfied by the actual state.
    Returns (passed, message).
    """
    cond_type = condition.get("condition")

    if cond_type == "exists_type":
        target_type = condition.get("type")
        props = condition.get("properties", {})

        for obj in actual_state:
            if obj.get("type") == target_type:
                # Check properties if specified
                if props:
                    obj_props = obj.get("properties", {})
                    all_match = True
                    for key, expected_val in props.items():
                        actual_val = obj_props.get(key)
                        if actual_val != expected_val:
                            all_match = False
                            break
                    if all_match:
                        return True, f"Found {target_type} with matching properties"
                else:
                    return True, f"Found {target_type}"

        return False, f"No {target_type} found in state"

    elif cond_type == "dag_children_of_cut":
        expected_children = condition.get("expected_children", [])

        for obj in actual_state:
            if obj.get("type") == "Part::Cut":
                children = obj.get("children", [])
                child_types = [actual_state[actual_state.index(c)] if isinstance(
                    c, str) else c.get("type") for c in children if isinstance(c, str)]

                # More robust check: look at children array for names, then find their types
                found_types = []
                for child_name in obj.get("children", []):
                    for o in actual_state:
                        if o.get("id") == child_name or o.get("name") == child_name:
                            found_types.append(o.get("type"))
                            break

                if found_types:
                    all_found = True
                    for expected in expected_children:
                        if expected not in found_types:
                            all_found = False
                            break
                    if all_found:
                        return True, f"Cut object has expected children: {found_types}"
                    else:
                        return False, f"Cut object missing expected children. Found: {found_types}, Expected: {expected_children}"

        return False, "No Part::Cut object found in state"

    return False, f"Unknown condition type: {cond_type}"


def run_evaluation(fixture: Dict[str, Any], adapter, verbose: bool = True) -> tuple[bool, str]:
    """
    Run a single evaluation fixture.
    Returns (passed, message).
    """
    name = fixture["name"]
    prompt = fixture["prompt"]
    expected_conditions = fixture.get("expected_state", [])

    if verbose:
        print(f"\n[RUNNING] {name}")
        print(f"  Prompt: {prompt}")

    try:
        # Step 1: Clear document for test isolation
        if verbose:
            print("  [Setup] Clearing document...")
        adapter.clear_document()

        # Step 2: Create fresh agent (clears ReAct scratchpad/history)
        agent = CADAgent(adapter=adapter)

        # Step 3: Run the prompt through the agent
        if verbose:
            print("  [Agent] Running prompt...")
        response = agent.handle_message(prompt)

        if verbose:
            print(
                f"  [Agent] Response: {response[:200]}{'...' if len(response) > 200 else ''}")

        # Step 4: Fetch final state
        if verbose:
            print("  [Verify] Fetching final state...")
        state_json = adapter.get_state()
        actual_state = json.loads(state_json)

        # Step 5: Run assertions
        if verbose:
            print(
                f"  [Verify] Checking {len(expected_conditions)} condition(s)...")

        all_passed = True
        messages = []

        for i, condition in enumerate(expected_conditions):
            passed, msg = check_condition(actual_state, condition)
            if not passed:
                all_passed = False
                messages.append(f"Condition {i+1} FAILED: {msg}")
            else:
                messages.append(f"Condition {i+1} PASSED: {msg}")

        if all_passed:
            return True, " | ".join(messages)
        else:
            return False, "; ".join(messages)

    except Exception as e:
        return False, f"Execution error: {type(e).__name__}: {str(e)}"


def main():
    print("=" * 60)
    print("PieCAD Evaluation Harness")
    print("=" * 60)

    # Load fixtures
    fixtures_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tests", "eval_fixtures.json")
    fixtures = load_fixtures(fixtures_path)
    print(f"Loaded {len(fixtures)} test fixtures")

    # Load adapter
    print("Loading FreeCAD Adapter (port 9876)...")
    try:
        adapter = load_adapter()
        # Test connection
        adapter.get_state()
        print("Adapter connected successfully")
    except Exception as e:
        print(f"FATAL: Could not connect to FreeCAD bridge: {e}")
        print("Ensure FreeCAD is running with the PieCAD macro on port 9876")
        sys.exit(1)

    # Run evaluations
    results = []
    for fixture in fixtures:
        passed, message = run_evaluation(fixture, adapter)
        results.append((fixture["name"], passed, message))
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {fixture['name']}")
        if not passed:
            print(f"    Reason: {message}")

    # Scoreboard
    passed_count = sum(1 for _, passed, _ in results if passed)
    total = len(results)

    print("\n" + "=" * 60)
    print("EVALUATION SCOREBOARD")
    print("=" * 60)
    for name, passed, msg in results:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    print("-" * 60)
    print(f"FINAL SCORE: {passed_count}/{total} PASSING")

    if passed_count == total:
        print("ALL TESTS PASSED!")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED!")
        sys.exit(1)


if __name__ == "__main__":
    main()
