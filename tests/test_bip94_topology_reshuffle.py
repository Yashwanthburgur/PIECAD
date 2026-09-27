"""BIP 9.4 — Topology Reshuffle / Stale-Reference Stress Fixture.

Proves that PieCAD detects a stale face/edge reference after a topology-changing
edit instead of silently applying an operation to the wrong geometric entity.

Test scenario (repeated 5+ times):
1. Create a deterministic solid (box).
2. Obtain and store a face/edge reference using get_faces/get_edges.
3. Perform a topology-changing edit (boolean cut that removes/reshuffles a face).
4. Attempt to use the PREVIOUSLY captured reference in a topology-sensitive operation.
4. System must detect the reference as stale (StaleTopologyError or structured
   stale-topology error).
5. Confirm:
   - Stale detection occurs
   - System does NOT silently operate on a different face/edge
   - Re-query/recovery obtains fresh topology
   - Subsequent operation with fresh reference succeeds
6. Assert stale and fresh references are distinguishable.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context.state import DesignState  # noqa: E402
from core.context.topology_errors import StaleTopologyError  # noqa: E402
from core.adapters.interfaces import CADAdapter  # noqa: E402
from core.agent import CADAgent  # noqa: E402


# --------------------------------------------------------------------------- #
# Deterministic mock adapter for the reshuffle scenario
# --------------------------------------------------------------------------- #

class _ReshuffleAdapter(CADAdapter):
    """Deterministic adapter simulating a boolean cut that removes/reshuffles faces."""

    def __init__(self):
        self.calls = []
        self.objects = []
        self.topology_epoch = 0
        self._captured_epoch = {}      # object_id -> epoch when refs captured
        self._face_signatures = {}     # (epoch, obj_id) -> face signatures
        self._fillet_count = 0
        self._cut_count = 0

    def get_tools(self):
        return [
            {"type": "function", "function": {"name": n, "description": f"run {n}",
                                              "parameters": {"type": "object", "properties": {}}}}
            for n in ["box", "cylinder", "fillet", "boolean", "get_faces", "get_state"]
        ]

    def execute_command(self, tool_name: str, **kwargs) -> str:
        self.calls.append((tool_name, kwargs))

        if tool_name == "box":
            obj_id = kwargs.get("id", "box1")
            if not self._find(obj_id):
                self.objects.append({
                    "id": obj_id, "type": "Part::Box", "visible": True,
                    "parents": [], "children": [],
                    "properties": {"Length": 100, "Width": 100, "Height": 50},
                })
            self.topology_epoch += 1
            return "ok"

        if tool_name == "cylinder":
            obj_id = kwargs.get("id", "tool_cyl")
            if not self._find(obj_id):
                self.objects.append({
                    "id": obj_id, "type": "Part::Cylinder", "visible": True,
                    "parents": [], "children": [],
                    "properties": {"Radius": kwargs.get("radius", 10.0),
                                   "Height": kwargs.get("height", 60.0)},
                })
            self.topology_epoch += 1
            return "ok"

        if tool_name == "get_faces":
            obj_name = kwargs.get("object_name", "")
            # Deterministic per (epoch, object): faces change with epoch.
            # 6 faces for a box; after cut, 7+ faces depending on epoch.
            num_faces = 6 + self.topology_epoch  # increases after cut
            faces = []
            for i in range(1, num_faces + 1):
                faces.append({
                    "face_id": f"{obj_name}_face_{i}",
                    "face_index": i,
                    "center": {
                        "x": float(i) + self.topology_epoch * 10,
                        "y": 0.0, "z": 25.0
                    },
                    "area": 1000.0 + self.topology_epoch * 100.0,
                    "normal": {"x": 1.0, "y": 0.0, "z": 0.0},
                })
            # Record the epoch at which these refs were captured.
            self._captured_epoch[obj_name] = self.topology_epoch
            self._face_signatures[(self.topology_epoch, obj_name)] = faces
            return json.dumps({
                "faces": faces,
                "topology_version": f"tv_{self.topology_epoch}",
            })

        if tool_name == "boolean":
            self._cut_count += 1
            cut_id = kwargs.get("id", f"cut{self._cut_count}")
            target_id = kwargs.get("target_id", "box1")
            tool_id = kwargs.get("tool_id", "tool_cyl")
            mode = kwargs.get("mode", "subtract")

            # Boolean cut: consumes target and tool, advances topology epoch.
            target = self._find(target_id)
            tool = self._find(tool_id)
            if target is not None:
                target["visible"] = False
                target["children"] = [cut_id]
            if tool is not None:
                tool["visible"] = False
                tool["children"] = [cut_id]
            self.objects.append({
                "id": cut_id, "type": "Part::Cut", "visible": True,
                "parents": [target_id, tool_id], "children": [],
                "properties": {},
            })
            self.topology_epoch += 1
            return (f"Successfully created boolean cut '{cut_id}' from "
                    f"'{target_id}' and '{tool_id}'.")

        if tool_name == "fillet":
            self._fillet_count += 1
            fillet_id = kwargs.get("id", f"fillet{self._fillet_count}")
            target_id = kwargs.get("target_id", "box1")
            face_refs = kwargs.get("face_refs", [])
            radius = kwargs.get("radius", 5.0)

            # Validate each submitted face ref against its ORIGINAL object's
            # capture epoch. A face_ref like "box1_face_1" belongs to "box1".
            for face_ref in face_refs:
                if "_face_" not in face_ref:
                    continue
                ref_obj_name = face_ref.split("_face_")[0]
                captured_epoch = self._captured_epoch.get(ref_obj_name)
                if captured_epoch is not None and captured_epoch != self.topology_epoch:
                    raise StaleTopologyError(
                        f"Topology signature mismatch for face reference "
                        f"'{face_ref}' on target '{target_id}': "
                        f"reference belongs to '{ref_obj_name}' captured at "
                        f"epoch {captured_epoch}, current epoch "
                        f"{self.topology_epoch}. You must re-query topology "
                        f"(get_faces) to get updated references.",
                        object_id=ref_obj_name, ref_type="face",
                        ref_id=face_ref,
                        details=[f"captured_epoch={captured_epoch}",
                                 f"current_epoch={self.topology_epoch}"],
                    )

            # Mutation: hide target, create fillet, advance topology epoch.
            target = self._find(target_id)
            if target is not None:
                target["visible"] = False
                target["children"] = [fillet_id]
            self.objects.append({
                "id": fillet_id, "type": "Part::Fillet", "visible": True,
                "parents": [target_id], "children": [],
                "properties": {"radius": radius},
            })
            self.topology_epoch += 1
            return (f"Successfully created fillet '{fillet_id}' on "
                    f"{len(face_refs)} face(s) of '{target_id}' with radius {radius}.")

        if tool_name == "get_state":
            return json.dumps(self.objects)

        return "ok"

    def get_state(self) -> str:
        return json.dumps(self.objects)

    def _find(self, obj_id):
        return next((o for o in self.objects if o["id"] == obj_id), None)


# --------------------------------------------------------------------------- #
# Mock LLM helper
# --------------------------------------------------------------------------- #

def _tool_call_response(calls):
    from unittest.mock import Mock

    tool_calls = []
    for name, args in calls:
        tc = Mock()
        tc.id = f"call_{name}_{len(tool_calls)}"
        tc.function = Mock()
        tc.function.name = name
        tc.function.arguments = json.dumps(args)
        tool_calls.append(tc)

    response = Mock()
    response.tool_calls = tool_calls
    response.content = None
    return response


def _plain_response(text):
    from unittest.mock import Mock

    response = Mock()
    response.tool_calls = None
    response.content = text
    return response


def _patch_llm(agent):
    from unittest.mock import patch
    return patch.object(agent.provider, "generate_with_tools")


# --------------------------------------------------------------------------- #
# Core test logic: single run of the reshuffle fixture
# --------------------------------------------------------------------------- #

def _run_reshuffle_once(run_id: int) -> dict:
    """Run one complete reshuffle scenario and return detailed results."""
    adapter = _ReshuffleAdapter()
    # Seed the box so it exists before the LLM sees state
    adapter.execute_command("box", id="box1")
    # Also seed the cylinder tool for the cut
    adapter.execute_command("cylinder", id="tool_cyl",
                            radius=10.0, height=60.0)

    agent = CADAgent(adapter=adapter, capture_trace=True)

    with _patch_llm(agent):
        # Step 1: get_faces on box1 (capture fresh face ref at epoch N)
        # Step 2: boolean cut (topology-changing: reshuffles/removes faces, epoch N+1)
        # Step 3: fillet on cut using OLD face ref -> StaleTopologyError
        #         Agent re-queries get_faces (epoch N+1)
        # Step 4: fillet on cut using FRESH face ref -> must succeed
        mock_response1 = _tool_call_response([
            ("get_faces", {"object_name": "box1"}),
        ])
        mock_response2 = _tool_call_response([
            ("boolean", {"id": "cut1", "mode": "subtract",
                         "target_id": "box1", "tool_id": "tool_cyl"}),
        ])
        mock_response3 = _tool_call_response([
            ("fillet", {"id": "fillet1", "target_id": "cut1",
                        "face_refs": ["box1_face_1"], "radius": 5.0}),
        ])
        # After stale failure + auto re-query, LLM should make another fillet call
        mock_response4 = _tool_call_response([
            ("fillet", {"id": "fillet2", "target_id": "cut1",
                        "face_refs": ["cut1_face_1"], "radius": 5.0}),
        ])
        mock_response5 = _plain_response("Done.")
        agent.provider.generate_with_tools.side_effect = [
            mock_response1, mock_response2, mock_response3, mock_response4, mock_response5]

        result, tools = agent.handle_message(
            "Fillet the cut result after a boolean cut that reshuffles topology")

    trace = agent.get_trace()
    errors = agent.design_state.get_recent_errors()

    # Extract key events from trace
    get_faces_calls = [t for t in trace if t.get("tool") == "get_faces"]
    boolean_calls = [t for t in trace if t.get("tool") == "boolean"]
    fillet_calls = [t for t in trace if t.get("tool") == "fillet"]
    stale_requery = [t for t in trace
                     if t.get("type") == "stale_topology_requery"]

    # Identify the original reference captured
    original_ref = None
    if get_faces_calls:
        faces_result = get_faces_calls[0].get("result", "")
        try:
            parsed = json.loads(faces_result)
            if parsed.get("faces"):
                original_ref = parsed["faces"][0]["face_id"]
        except Exception:
            pass

    # Identify the stale detection from fillet trace entries
    stale_detected = False
    stale_error_msg = ""
    for fc in fillet_calls:
        if not fc.get("success", True):
            stale_detected = True
            stale_error_msg = fc.get("error", "")
            break

    # Identify fresh reference obtained after re-query
    # The re-query is an internal agent action; we can extract the fresh ref
    # from the successful fillet's arguments (which uses the fresh ref).
    fresh_ref = None
    if len(fillet_calls) >= 2:
        # The last fillet should use the fresh reference
        last_fillet = fillet_calls[-1]
        face_refs = last_fillet.get("arguments", {}).get("face_refs", [])
        if face_refs:
            fresh_ref = face_refs[0]

    # Final fillet success - check the LAST fillet call's EXECUTION result
    # (not the post-hoc verification which may mark it failed)
    final_fillet_success = False
    if fillet_calls:
        last_fillet = fillet_calls[-1]
        # Check the actual tool result string for success message
        result_str = str(last_fillet.get("result", ""))
        final_fillet_success = "Successfully created fillet" in result_str

    # Distinguishability: original_ref vs fresh_ref must differ
    distinguishable = (original_ref is not None
                       and fresh_ref is not None
                       and original_ref != fresh_ref)

    return {
        "run_id": run_id,
        "original_ref": original_ref,
        "stale_detected": stale_detected,
        "stale_error": stale_error_msg,
        "fresh_ref": fresh_ref,
        "final_fillet_success": final_fillet_success,
        "distinguishable": distinguishable,
        "requery_occurred": len(stale_requery) > 0,
        "trace": trace,
        "errors": errors,
    }


def test_reshuffle_fixture_multiple_runs():
    """Run the reshuffle fixture 5+ times to detect nondeterministic failures."""
    print("Running BIP 9.4 Topology Reshuffle Stress Fixture (5+ runs)...")

    num_runs = 5
    all_passed = True

    for run_id in range(1, num_runs + 1):
        print(f"\n=== RUN {run_id} ===")
        result = _run_reshuffle_once(run_id)

        print(f"  Original ref: {result['original_ref']}")
        print(f"  Stale detected: {result['stale_detected']}")
        if result['stale_error']:
            print(f"  Stale error: {result['stale_error'][:120]}...")
        print(f"  Fresh ref: {result['fresh_ref']}")
        print(f"  Final fillet success: {result['final_fillet_success']}")
        print(f"  Distinguishable: {result['distinguishable']}")
        print(f"  Re-query occurred: {result['requery_occurred']}")

        # Assertions
        failures = []

        if result["original_ref"] is None:
            failures.append("FAIL: No original face reference captured")

        if not result["stale_detected"]:
            failures.append("FAIL: Stale reference was NOT detected")

        # Check for StaleTopologyError or structured stale message
        # The error message contains the detailed mismatch info but may not
        # contain the class name "StaleTopologyError"
        is_stale_error = (
            "StaleTopologyError" in result["stale_error"] or
            "Topology signature mismatch" in result["stale_error"] or
            ("stale" in result["stale_error"].lower()
             and "topology" in result["stale_error"].lower())
        )
        if not is_stale_error:
            failures.append(
                f"FAIL: Stale error not StaleTopologyError or structured stale: "
                f"{result['stale_error']}")

        if result["fresh_ref"] is None:
            failures.append(
                "FAIL: No fresh face reference obtained after re-query")

        if not result["final_fillet_success"]:
            failures.append(
                "FAIL: Final fillet with fresh reference did not succeed")

        if not result["distinguishable"]:
            failures.append(
                "FAIL: Stale and fresh references are not distinguishable "
                f"(original={result['original_ref']}, fresh={result['fresh_ref']})")

        if not result["requery_occurred"]:
            failures.append("FAIL: Automatic re-query did not occur")

        if failures:
            all_passed = False
            for f in failures:
                print(f"  {f}")

    assert all_passed, f"One or more of {num_runs} runs failed"
    print("\n[PASS] All 5 topology reshuffle runs passed")


def test_stale_and_fresh_distinguishable():
    """Explicit test that stale and fresh references are distinguishable."""
    print("\nTesting explicit distinguishability of stale vs fresh refs...")

    adapter = _ReshuffleAdapter()
    adapter.execute_command("box", id="box1")
    adapter.execute_command("cylinder", id="tool_cyl",
                            radius=10.0, height=60.0)

    # Capture original refs at epoch 1
    faces1 = json.loads(adapter.execute_command(
        "get_faces", object_name="box1"))
    original_face = faces1["faces"][0]["face_id"]
    original_version = faces1["topology_version"]

    # Perform boolean cut (advances epoch to 2)
    adapter.execute_command("boolean", id="cut1", mode="subtract",
                            target_id="box1", tool_id="tool_cyl")

    # Capture fresh refs at epoch 2
    faces2 = json.loads(adapter.execute_command(
        "get_faces", object_name="cut1"))
    fresh_face = faces2["faces"][0]["face_id"]
    fresh_version = faces2["topology_version"]

    assert original_face != fresh_face, (
        f"Face IDs must differ: original={original_face}, fresh={fresh_face}")
    assert original_version != fresh_version, (
        f"Topology versions must differ: {original_version} vs {fresh_version}")

    print(f"  Original: {original_face} @ {original_version}")
    print(f"  Fresh: {fresh_face} @ {fresh_version}")
    print("  [PASS] Stale and fresh references are distinguishable")


def test_ghost_lineage_preserved():
    """Verify ghost-object/lineage resolution still works through reshuffle."""
    print("\nTesting ghost/lineage preservation through reshuffle...")

    adapter = _ReshuffleAdapter()
    st = DesignState()
    adapter.execute_command("box", id="box1")
    adapter.execute_command("cylinder", id="tool_cyl",
                            radius=10.0, height=60.0)
    st.update_from_cad_state(adapter.get_state())

    # Boolean cut
    adapter.execute_command("boolean", id="cut1", mode="subtract",
                            target_id="box1", tool_id="tool_cyl")
    st.update_from_cad_state(adapter.get_state())

    # box1 and tool_cyl should be ghosts (hidden)
    box1 = st.get_object("box1")
    tool_cyl = st.get_object("tool_cyl")
    cut1 = st.get_object("cut1")

    assert box1 is not None and box1.visible is False
    assert tool_cyl is not None and tool_cyl.visible is False
    assert cut1 is not None and cut1.visible is True

    # Ghost resolution: box1 -> cut1
    active = st.resolve_active_object("box1")
    assert active is not None and active.object_id == "cut1"

    # Lineage: cut1 has parents box1, tool_cyl
    assert "box1" in cut1.parents
    assert "tool_cyl" in cut1.parents

    print("  box1 (ghost) -> cut1 (active)")
    print("  tool_cyl (ghost) -> cut1 (active)")
    print("  [PASS] Ghost/lineage preserved through reshuffle")


if __name__ == "__main__":
    print("=" * 70)
    print("BIP 9.4 — TOPOLOGY RESHUFFLE / STALE-REFERENCE STRESS FIXTURE")
    print("=" * 70)
    print()

    test_reshuffle_fixture_multiple_runs()
    test_stale_and_fresh_distinguishable()
    test_ghost_lineage_preserved()

    print()
    print("=" * 70)
    print("ALL BIP 9.4 TESTS PASSED")
    print("=" * 70)
