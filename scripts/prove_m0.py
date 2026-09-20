#!/usr/bin/env python
"""M0 Final Proof: GLB export and external MCP tool routing.

Standalone backend-level proof script that bypasses the LLM entirely. It
directly drives the FreeCADAdapter to:

  1. Test 1 - GLB Round-Trip: create a box and export the visible state to a
     GLB file, asserting the file is created and non-empty.
  2. Test 2 - MCP End-to-End: create a sketch and route an advanced
     ``partdesign_sketch_constraint`` tool through the external MCP server
     client, asserting the call does not raise.

Run with:
    python scripts/prove_m0.py
"""
import os
import sys

# Make the project root importable regardless of CWD.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from adapters.freecad.adapter import FreeCADAdapter  # noqa: E402


def main() -> int:
    # STEP 1: initialize the adapter and ensure the exports directory exists.
    adapter = FreeCADAdapter()
    os.makedirs("exports", exist_ok=True)

    # ------------------------------------------------------------------ #
    # Test 1 - GLB Round-Trip
    # ------------------------------------------------------------------ #
    # Use an absolute path: a relative path ("exports/...") can resolve to
    # FreeCAD's own installation directory and cause a write-permission error.
    glb_path = os.path.abspath("exports/m0_proof.glb")
    try:
        adapter.execute_command(
            "box", id="proof_box", length=10, width=10, height=10)

        # The GLB export is exposed as the adapter's dedicated method (it uses
        # the bridge's export_current_state GLB/OBJ logic). If present it is
        # used; otherwise fall back to the execute_command route used by agents.
        if hasattr(adapter, "export_state_model"):
            adapter.export_state_model(glb_path, "glb")
        else:  # pragma: no cover - defensive fallback
            adapter.execute_command(
                "export_state_model", format="glb", filepath=glb_path)

        if not os.path.exists(glb_path):
            print(f"[FAIL] GLB file was not created: {glb_path}")
            return 1
        if os.path.getsize(glb_path) <= 0:
            print(f"[FAIL] GLB file is empty (0 bytes): {glb_path}")
            return 1
        print(f"[PASS] GLB Export Successful ({glb_path}, "
              f"{os.path.getsize(glb_path)} bytes)")
    except Exception as exc:  # pragma: no cover - only on failure
        print(f"[FAIL] GLB Export: {type(exc).__name__}: {exc}")
        return 1

    # ------------------------------------------------------------------ #
    # Test 2 - MCP End-to-End
    # ------------------------------------------------------------------ #
    try:
        adapter.execute_command(
            "sketch",
            id="proof_sketch",
            # The box created in Test 1 (`proof_box`) supplies a valid face ref
            # in the ObjectName_face_N format the sketch tool expects.
            face_ref="proof_box_face_1",
            shapes=[],
        )

        adapter.execute_command(
            "partdesign_sketch_constraint",
            sketch_id="proof_sketch",
            constraint_type="DistanceX",
            elements=[{"edge_index": 1}],
            value=15.0,
        )
        print("[PASS] MCP Tool Routing Successful")
    except Exception as exc:  # pragma: no cover - only on failure
        print(f"[FAIL] MCP Tool Routing: {type(exc).__name__}: {exc}")
        return 1

    print("[M0 COMPLETE] All backend systems nominal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
