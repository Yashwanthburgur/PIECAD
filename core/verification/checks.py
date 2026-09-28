"""
Agnostic Geometric Verification Engine

Provides CAD-agnostic, mathematically-grounded geometric verification methods.
These checks operate on JSON output from adapter tools (get_mass_properties, get_faces)
and contain zero CAD-system-specific terminology or imports.
"""
import json
import math
from enum import Enum
from typing import List, Dict, Any, Tuple, Optional


class VerificationResult(Enum):
    """Result of a parameter verification."""
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
# Centralized Tolerance Configuration
# --------------------------------------------------------------------------- #
# All tolerances are in millimeters unless otherwise noted.
# Tolerances are chosen to be tighter than typical manufacturing tolerances
# while accommodating FreeCAD's internal floating-point representation.
#
# DIMENSIONAL_TOLERANCE: General dimensional comparisons (length, width, height,
# radius, diameter, depth, distance). 1e-3 mm = 1 micron, well within typical
# CAD kernel precision.
# ANGULAR_TOLERANCE: Angular comparisons in degrees. 1e-3 degrees approx 17 µrad.
# POSITION_TOLERANCE: Position/origin comparisons. Same as dimensional.
# VOLUME_TOLERANCE: Volume comparisons relative to magnitude. 1e-6 relative.
# COUNT_TOLERANCE: Exact integer comparison (patterns). No tolerance.
# DEPTH_TOLERANCE: Depth comparisons for holes/pockets. 1e-3 mm.
# THREAD_TOLERANCE: Thread parameter comparisons. 1e-3 mm.
DIMENSIONAL_TOLERANCE = 1e-3
ANGULAR_TOLERANCE = 1e-3
POSITION_TOLERANCE = 1e-3
VOLUME_RELATIVE_TOLERANCE = 1e-6
COUNT_TOLERANCE = 0  # Exact integer match
DEPTH_TOLERANCE = 1e-3
THREAD_TOLERANCE = 1e-3


def _float_equal(a: float, b: float, tol: float = DIMENSIONAL_TOLERANCE) -> bool:
    """Compare two floats with absolute tolerance."""
    return abs(a - b) <= tol


def _float_equal_rel(a: float, b: float, tol: float = VOLUME_RELATIVE_TOLERANCE) -> bool:
    """Compare two floats with relative tolerance (for volume)."""
    if a == 0 and b == 0:
        return True
    return abs(a - b) / max(abs(a), abs(b)) <= tol


def _int_equal(a: int, b: int) -> bool:
    """Exact integer comparison."""
    return a == b


class _Vector3:
    """Simple 3D vector for CAD-agnostic geometric calculations."""

    __slots__ = ("x", "y", "z")

    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __sub__(self, other: "_Vector3") -> "_Vector3":
        return _Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __add__(self, other: "_Vector3") -> "_Vector3":
        return _Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def dot(self, other: "_Vector3") -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: "_Vector3") -> "_Vector3":
        return _Vector3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def multiply(self, scalar: float) -> "_Vector3":
        return _Vector3(self.x * scalar, self.y * scalar, self.z * scalar)

    @property
    def Length(self) -> float:
        return math.sqrt(self.x ** 2 + self.y ** 2 + self.z ** 2)

    def normalize(self) -> None:
        length = self.Length
        if length > 0:
            self.x /= length
            self.y /= length
            self.z /= length


def _parse_vec(data: Dict[str, float], default: float = 0.0) -> _Vector3:
    """Parse a dict with x, y, z keys into a _Vector3."""
    return _Vector3(
        float(data.get("x", default)),
        float(data.get("y", default)),
        float(data.get("z", default)),
    )


class GeometryVerifier:
    """
    Static utility class for verifying geometric invariants.

    All methods accept JSON strings as produced by standard adapter tools:
    - get_mass_properties: {"status": "success", "volume": float, "bounding_box": {...}, ...}
    - get_faces: [{"face_id": str, "face_index": int, "center": {...}, "area": float}, ...]
    """

    @staticmethod
    def verify_exists(final_mass_json: str):
        """
        Verify that a final solid exists and has positive volume.

        Args:
            final_mass_json: JSON from get_mass_properties on the final solid.

        Returns:
            A tuple ``(bool, reason)``. The bool is True if the JSON parses and
            its Volume is strictly greater than 0.0, False otherwise. ``reason``
            is a specific human-readable string explaining any failure.
        """
        try:
            data = json.loads(final_mass_json)
        except (json.JSONDecodeError, AttributeError, TypeError, KeyError) as e:
            return False, f"final_mass_json did not parse as JSON: {e}"
        if not isinstance(data, dict):
            return False, f"Expected a dict from get_mass_properties, got {type(data).__name__}"
        volume = data.get("Volume", data.get("volume", 0.0))
        try:
            volume = float(volume)
        except (TypeError, ValueError):
            volume = 0.0
        if volume <= 0.0:
            return False, f"Volume {volume} was not > 0 (degenerate/empty solid)."
        return True, "solid exists with positive volume"

    @staticmethod
    def verify_volume_reduction(base_mass_json: str, cut_mass_json: str):
        """
        Verify that a boolean subtraction/hole operation actually removed material.

        Args:
            base_mass_json: JSON from get_mass_properties on the base solid (before cut).
            cut_mass_json: JSON from get_mass_properties on the resulting solid (after cut).

        Returns:
            A tuple ``(bool, reason)``. True if volume strictly decreased, False
            otherwise (including parse errors), with a specific reason string.
        """
        try:
            base = json.loads(base_mass_json)
            cut = json.loads(cut_mass_json)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return False, f"base/cut mass JSON did not parse: {e}"
        if not isinstance(base, dict) or not isinstance(cut, dict):
            return False, "base/cut mass properties were not dicts"
        base_vol = float(base.get("volume", 0.0))
        cut_vol = float(cut.get("volume", 0.0))

        # Volume must be strictly less after a cut operation
        if not (cut_vol < base_vol):
            return False, (
                f"Volume {cut_vol} did not decrease below base {base_vol}"
                "(material may not have been removed)."
            )
        return True, f"volume reduced from {base_vol} to {cut_vol}"

    @staticmethod
    def verify_face_count_increase(base_faces_json: str, op_faces_json: str):
        """
        Verify that an operation (chamfer, fillet, patterned holes) increased face count.

        Args:
            base_faces_json: JSON from get_faces on the base solid.
            op_faces_json: JSON from get_faces on the operated solid.

        Returns:
            A tuple ``(bool, reason)``. True if face count strictly increased,
            False otherwise, with a specific reason string.
        """
        try:
            base_faces = json.loads(base_faces_json)
            op_faces = json.loads(op_faces_json)
        except (json.JSONDecodeError, TypeError) as e:
            return False, f"face JSON did not parse: {e}"
        base_count = len(base_faces) if isinstance(base_faces, list) else 0
        op_count = len(op_faces) if isinstance(op_faces, list) else 0
        if not (op_count > base_count):
            return False, (
                f"Face count {op_count} did not increase above base {base_count}"
                "(operation may not have applied)."
            )
        return True, f"face count increased from {base_count} to {op_count}"

    @staticmethod
    def verify_within_bounding_box(part_mass_json: str, max_x: float, max_y: float, max_z: float) -> bool:
        """
        Verify that a part's bounding box dimensions do not exceed specified maximums.

        Args:
            part_mass_json: JSON from get_mass_properties on the part.
            max_x: Maximum allowed extent in X direction.
            max_y: Maximum allowed extent in Y direction.
            max_z: Maximum allowed extent in Z direction.

        Returns:
            True if all dimensions are within bounds, False otherwise.
        """
        try:
            data = json.loads(part_mass_json)
            bbox = data.get("bounding_box", {})

            x_min = bbox.get("XMin", 0.0)
            x_max = bbox.get("XMax", 0.0)
            y_min = bbox.get("YMin", 0.0)
            y_max = bbox.get("YMax", 0.0)
            z_min = bbox.get("ZMin", 0.0)
            z_max = bbox.get("ZMax", 0.0)

            x_span = abs(x_max - x_min)
            y_span = abs(y_max - y_min)
            z_span = abs(z_max - z_min)

            return (x_span <= max_x) and (y_span <= max_y) and (z_span <= max_z)
        except (json.JSONDecodeError, KeyError, TypeError):
            return False

    @staticmethod
    def verify_mate_coincident(moving_face_json: str, fixed_face_json: str,
                               tolerance: float = 1e-3) -> Tuple[bool, str]:
        """
        Verify that a coincident mate actually produced coplanar, opposing faces.

        Args:
            moving_face_json: JSON from get_faces on the moving object's mated face.
                Expected format: {"face_id": str, "center": {"x": float, "y": float, "z": float},
                                  "normal": {"x": float, "y": float, "z": float}, "area": float}
            fixed_face_json: JSON from get_faces on the fixed object's mated face.
                Same format as moving_face_json.
            tolerance: Maximum allowed deviation (mm) for coplanarity check.

        Returns:
            A tuple ``(bool, reason)``. True if faces are coplanar with opposing normals
            within tolerance, False otherwise with specific reason.
        """
        try:
            moving = json.loads(moving_face_json)
            fixed = json.loads(fixed_face_json)
        except (json.JSONDecodeError, TypeError) as e:
            return False, f"face JSON did not parse: {e}"

        if not isinstance(moving, dict) or not isinstance(fixed, dict):
            return False, "face data was not dicts"

        # Extract centers and normals
        try:
            m_center = moving.get("center", {})
            f_center = fixed.get("center", {})
            m_normal = moving.get("normal", {})
            f_normal = fixed.get("normal", {})

            m_c = _parse_vec(m_center)
            f_c = _parse_vec(f_center)
            m_n = _parse_vec(m_normal)
            f_n = _parse_vec(f_normal)
        except Exception:
            return False, "invalid center/normal data"

        # Check normals are opposing (dot product approx -1)
        if abs(m_n.Length - 1.0) > 1e-6:
            m_n.normalize()
        if abs(f_n.Length - 1.0) > 1e-6:
            f_n.normalize()

        dot = m_n.dot(f_n)
        if dot > -0.9999:  # Should be approx -1 for opposing faces
            return False, f"face normals not opposing (dot={dot:.4f})"

        # Check coplanarity: distance from moving face center to fixed face plane
        plane_dist = abs((m_c - f_c).dot(f_n))
        if plane_dist > tolerance:
            return False, f"faces not coplanar (distance={plane_dist:.6f} > {tolerance})"

        return True, f"coincident mate verified (distance={plane_dist:.6f})"

    @staticmethod
    def verify_mate_concentric(moving_edge_json: str, fixed_edge_json: str,
                               tolerance: float = 1e-3) -> Tuple[bool, str]:
        """
        Verify that a concentric mate actually produced coaxial cylinders.

        Args:
            moving_edge_json: JSON from get_edges on the moving object's mated edge.
                Expected format: {"edge_id": str, "center": {"x": float, "y": float, "z": float},
                                  "axis": {"x": float, "y": float, "z": float}, "length": float}
            fixed_edge_json: JSON from get_edges on the fixed object's mated edge.
                Same format as moving_edge_json.
            tolerance: Maximum allowed deviation (mm) for axis alignment and center offset.

        Returns:
            A tuple ``(bool, reason)``. True if edges are coaxial within tolerance,
            False otherwise with specific reason.
        """
        try:
            moving = json.loads(moving_edge_json)
            fixed = json.loads(fixed_edge_json)
        except (json.JSONDecodeError, TypeError) as e:
            return False, f"edge JSON did not parse: {e}"

        if not isinstance(moving, dict) or not isinstance(fixed, dict):
            return False, "edge data was not dicts"

        # Extract centers and axes
        try:
            m_center = moving.get("center", {})
            f_center = fixed.get("center", {})
            m_axis = moving.get("axis", {})
            f_axis = fixed.get("axis", {})

            m_c = _parse_vec(m_center)
            f_c = _parse_vec(f_center)
            m_a = _parse_vec(m_axis)
            f_a = _parse_vec(f_axis)
        except Exception:
            return False, "invalid center/axis data"

        # Check axes are parallel (cross product length approx 0)
        cross_len = m_a.cross(f_a).Length
        if cross_len > tolerance:
            return False, f"axes not parallel (cross={cross_len:.6f} > {tolerance})"

        # Check centers are aligned along axis (perpendicular distance approx 0)
        # Vector between centers
        center_diff = m_c - f_c
        # Project onto axis
        axis_proj = center_diff.dot(f_a)
        perp_dist = (center_diff - f_a.multiply(axis_proj)).Length

        if perp_dist > tolerance:
            return False, f"centers not aligned (perp_dist={perp_dist:.6f} > {tolerance})"

        return True, f"concentric mate verified (axis_cross={cross_len:.6f}, perp_dist={perp_dist:.6f})"


# --------------------------------------------------------------------------- #
# Parameter-Level Verification (BIP 11.1)
# --------------------------------------------------------------------------- #

class ParameterVerifier:
    """
    Verifies that requested operation parameters match actual CAD results.

    All methods return VerificationResult:
    - PASS: requested parameter matches live CAD property/geometry within tolerance
    - FAIL: requested parameter demonstrably differs from live CAD
    - UNKNOWN: evidence unavailable or insufficient to decide
    """

    @staticmethod
    def _get_property(props: Dict[str, Any], key: str) -> Optional[float]:
        """Extract a float property from a properties dict."""
        if not isinstance(props, dict):
            return None
        val = props.get(key)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def verify_box_parameters(
        requested: Dict[str, Any],
        actual_props: Dict[str, Any]
    ) -> Tuple[VerificationResult, str]:
        """
        Verify box dimensions match requested values.

        Args:
            requested: Dict with requested "length", "width", "height"
            actual_props: Live properties dict from CAD state (keys: Length, Width, Height)

        Returns:
            (VerificationResult, reason)
        """
        failures = []
        for req_key, actual_key in [("length", "Length"), ("width", "Width"), ("height", "Height")]:
            req_val = requested.get(req_key)
            act_val = ParameterVerifier._get_property(actual_props, actual_key)
            if req_val is None:
                continue
            if act_val is None:
                return VerificationResult.UNKNOWN, f"Property {actual_key} unavailable in live state"
            if not _float_equal(float(req_val), act_val):
                failures.append(
                    f"{req_key}: requested {req_val}, got {act_val}")
        if failures:
            return VerificationResult.FAIL, "; ".join(failures)
        return VerificationResult.PASS, "box dimensions match requested values"

    @staticmethod
    def verify_cylinder_parameters(
        requested: Dict[str, Any],
        actual_props: Dict[str, Any]
    ) -> Tuple[VerificationResult, str]:
        """
        Verify cylinder radius and height match requested values.

        Args:
            requested: Dict with requested "radius", "height"
            actual_props: Live properties dict from CAD state (keys: Radius, Height)

        Returns:
            (VerificationResult, reason)
        """
        failures = []
        for req_key, actual_key in [("radius", "Radius"), ("height", "Height")]:
            req_val = requested.get(req_key)
            act_val = ParameterVerifier._get_property(actual_props, actual_key)
            if req_val is None:
                continue
            if act_val is None:
                return VerificationResult.UNKNOWN, f"Property {actual_key} unavailable in live state"
            if not _float_equal(float(req_val), act_val):
                failures.append(
                    f"{req_key}: requested {req_val}, got {act_val}")
        if failures:
            return VerificationResult.FAIL, "; ".join(failures)
        return VerificationResult.PASS, "cylinder dimensions match requested values"

    @staticmethod
    def verify_hole_parameters(
        requested: Dict[str, Any],
        hole_mass_json: str,
        drill_props: Optional[Dict[str, Any]] = None
    ) -> Tuple[VerificationResult, str]:
        """
        Verify hole parameters where evidence permits.

        Evidence sources:
        - hole_mass_json: get_mass_properties on the resulting cut object
        - drill_props: live properties of the drill tool (Radius, Height) if available

        Args:
            requested: Dict with "diameter", "depth", "kind", "thread_spec"
            hole_mass_json: get_mass_properties result on the hole result object
            drill_props: Optional properties of the drill tool object

        Returns:
            (VerificationResult, reason)
        """
        failures = []
        unknowns = []

        # Verify diameter via drill tool if available
        req_diameter = requested.get("diameter")
        if req_diameter is not None:
            if drill_props:
                act_radius = ParameterVerifier._get_property(
                    drill_props, "Radius")
                if act_radius is not None:
                    exp_radius = float(req_diameter) / 2.0
                    if not _float_equal(exp_radius, act_radius):
                        failures.append(
                            f"diameter: requested {req_diameter}, drill radius implies {act_radius * 2}")
                else:
                    unknowns.append("diameter (drill radius unavailable)")
            else:
                unknowns.append("diameter (drill tool properties unavailable)")

        # Verify depth via drill tool Height if available
        req_depth = requested.get("depth")
        if req_depth is not None:
            if drill_props:
                act_height = ParameterVerifier._get_property(
                    drill_props, "Height")
                if act_height is not None:
                    # Drill height = depth + 2mm over-drill (coplanar-face hardening)
                    exp_height = float(req_depth) + 2.0
                    if not _float_equal(exp_height, act_height):
                        failures.append(
                            f"depth: requested {req_depth}, drill height implies {act_height - 2.0} (with +2mm over-drill)")
                else:
                    unknowns.append("depth (drill height unavailable)")
            else:
                unknowns.append("depth (drill tool properties unavailable)")

        # Thread parameters - verify ThreadSpec property on cut object if available
        req_kind = requested.get("kind", "simple")
        req_thread = requested.get("thread_spec")
        if req_kind == "tapped" and req_thread:
            # Try to get ThreadSpec from the cut object's properties (from hole_mass_json)
            try:
                hole_data = json.loads(hole_mass_json)
                hole_props = hole_data.get("properties", {})
                act_thread_spec = hole_props.get("ThreadSpec")
                if act_thread_spec is not None:
                    # Normalize both for comparison (same logic as bridge)
                    import re

                    def normalize(s):
                        s = str(s).strip().upper()
                        s = s.replace(" ", "").replace("X", "x")
                        if "UNC" in s:
                            s = s.replace("UNC", " UNC")
                        s = " ".join(s.split())
                        return s
                    req_norm = normalize(req_thread)
                    act_norm = normalize(act_thread_spec)
                    if req_norm != act_norm:
                        failures.append(
                            f"thread_spec: requested {req_thread}, got {act_thread_spec}")
                else:
                    unknowns.append(
                        "thread_spec (ThreadSpec property not on cut object)")
            except (json.JSONDecodeError, TypeError):
                unknowns.append(
                    "thread_spec (could not parse hole mass properties)")

        if failures:
            return VerificationResult.FAIL, "; ".join(failures)
        if unknowns and not failures:
            return VerificationResult.UNKNOWN, "; ".join(unknowns)
        return VerificationResult.PASS, "hole parameters match where evidence available"

    @staticmethod
    def verify_fillet_parameters(
        requested: Dict[str, Any],
        fillet_props: Optional[Dict[str, Any]] = None
    ) -> Tuple[VerificationResult, str]:
        """
        Verify fillet radius. FreeCAD Part::Fillet stores radii per-edge in Edges list,
        not as a single Radius property. Evidence may be limited.

        Args:
            requested: Dict with "radius"
            fillet_props: Live properties of the fillet feature (if available)

        Returns:
            (VerificationResult, reason)
        """
        req_radius = requested.get("radius")
        if req_radius is None:
            return VerificationResult.UNKNOWN, "no radius requested"
        # Part::Fillet has no .Radius property; radii stored per-edge in Edges list.
        # We cannot reliably extract individual edge radii from properties dict alone.
        # Bridge could expose them but current state doesn't.
        return VerificationResult.UNKNOWN, "fillet radius verification requires per-edge Edges list access (not in properties)"

    @staticmethod
    def verify_chamfer_parameters(
        requested: Dict[str, Any],
        chamfer_props: Optional[Dict[str, Any]] = None
    ) -> Tuple[VerificationResult, str]:
        """
        Verify chamfer size. Part::Chamfer stores distances per-edge in Edges list.

        Args:
            requested: Dict with "size"
            chamfer_props: Live properties of the chamfer feature

        Returns:
            (VerificationResult, reason)
        """
        req_size = requested.get("size")
        if req_size is None:
            return VerificationResult.UNKNOWN, "no size requested"
        # Part::Chamfer has no .Size property; distances stored per-edge.
        return VerificationResult.UNKNOWN, "chamfer size verification requires per-edge Edges list access (not in properties)"

    @staticmethod
    def verify_pattern_count(
        requested: Dict[str, Any],
        pattern_props: Optional[Dict[str, Any]] = None,
        target_faces_json: Optional[str] = None
    ) -> Tuple[VerificationResult, str]:
        """
        Verify pattern occurrence count.

        For linear/circular patterns, the count is a construction parameter.
        Best evidence: if pattern was created via multiFuse of N copies,
        face count increase correlates but is not exact.
        FreeCAD pattern features don't expose count as a queryable property.

        Args:
            requested: Dict with "count"
            pattern_props: Live properties (no count property exposed)
            target_faces_json: get_faces result for the pattern result (optional)

        Returns:
            (VerificationResult, reason)
        """
        req_count = requested.get("count")
        if req_count is None:
            return VerificationResult.UNKNOWN, "no count requested"
        # No reliable property exposes the pattern count in current FreeCAD representation.
        # Face count increase is a proxy but not exact (depends on geometry).
        return VerificationResult.UNKNOWN, "pattern count not exposed as queryable property in current CAD representation"

    @staticmethod
    def verify_boolean_operation(
        requested: Dict[str, Any],
        base_mass_json: str,
        result_mass_json: str
    ) -> Tuple[VerificationResult, str]:
        """
        Verify boolean operation type matches volume change.

        Args:
            requested: Dict with "mode" (subtract/union/intersect)
            base_mass_json: get_mass_properties on base (before)
            result_mass_json: get_mass_properties on result (after)

        Returns:
            (VerificationResult, reason)
        """
        mode = requested.get("mode")
        if not mode:
            return VerificationResult.UNKNOWN, "no mode specified"
        try:
            base = json.loads(base_mass_json)
            result = json.loads(result_mass_json)
        except (json.JSONDecodeError, TypeError):
            return VerificationResult.UNKNOWN, "mass properties JSON did not parse"
        base_vol = float(base.get("volume", 0.0))
        result_vol = float(result.get("volume", 0.0))

        if mode == "subtract":
            # Volume must decrease
            if result_vol < base_vol:
                return VerificationResult.PASS, f"boolean subtract: volume decreased ({base_vol} -> {result_vol})"
            else:
                return VerificationResult.FAIL, f"boolean subtract expected volume decrease, got {base_vol} -> {result_vol}"
        elif mode == "union":
            # Volume must increase (or stay same if disjoint)
            if result_vol >= base_vol:
                return VerificationResult.PASS, f"boolean union: volume non-decreased ({base_vol} -> {result_vol})"
            else:
                return VerificationResult.FAIL, f"boolean union expected volume increase, got {base_vol} -> {result_vol}"
        elif mode == "intersect":
            # Volume must decrease (intersection is subset)
            if result_vol < base_vol:
                return VerificationResult.PASS, f"boolean intersect: volume decreased ({base_vol} -> {result_vol})"
            else:
                return VerificationResult.FAIL, f"boolean intersect expected volume decrease, got {base_vol} -> {result_vol}"
        else:
            return VerificationResult.UNKNOWN, f"unknown boolean mode: {mode}"

    @staticmethod
    def verify_operation(
        tool: str,
        args: Dict[str, Any],
        adapter: Any,
        result_id: Optional[str] = None,
        target_id: Optional[str] = None
    ) -> Tuple[VerificationResult, str]:
        """
        Dispatch to the appropriate parameter verifier for the given tool.

        Args:
            tool: Tool name (box, cylinder, hole, fillet, chamfer, pattern_linear,
                  pattern_circular, boolean)
            args: Tool arguments as passed to the operation
            adapter: CADAdapter instance for querying live state
            result_id: ID of the created/modified result object
            target_id: ID of the target object

        Returns:
            (VerificationResult, reason)
        """
        if tool == "box":
            if not result_id:
                return VerificationResult.UNKNOWN, "no result_id for box"
            props_json = adapter.execute_command(
                "get_mass_properties", object_name=result_id)
            try:
                props = json.loads(props_json)
                actual_props = props.get("properties", {})
            except (json.JSONDecodeError, TypeError):
                return VerificationResult.UNKNOWN, "could not parse box properties"
            return ParameterVerifier.verify_box_parameters(args, actual_props)

        elif tool == "cylinder":
            if not result_id:
                return VerificationResult.UNKNOWN, "no result_id for cylinder"
            props_json = adapter.execute_command(
                "get_mass_properties", object_name=result_id)
            try:
                props = json.loads(props_json)
                actual_props = props.get("properties", {})
            except (json.JSONDecodeError, TypeError):
                return VerificationResult.UNKNOWN, "could not parse cylinder properties"
            return ParameterVerifier.verify_cylinder_parameters(args, actual_props)

        elif tool == "hole":
            if not result_id or not target_id:
                return VerificationResult.UNKNOWN, "missing result_id or target_id for hole"
            hole_mass_json = adapter.execute_command(
                "get_mass_properties", object_name=result_id)
            # Try to get drill tool properties
            drill_props = None
            if target_id:
                # The drill tool is named {result_id}_drill
                try:
                    drill_props_json = adapter.execute_command(
                        "get_mass_properties", object_name=f"{result_id}_drill")
                    drill_props = json.loads(
                        drill_props_json).get("properties", {})
                except (json.JSONDecodeError, TypeError, Exception):
                    pass
            return ParameterVerifier.verify_hole_parameters(args, hole_mass_json, drill_props)

        elif tool == "fillet":
            if not result_id:
                return VerificationResult.UNKNOWN, "no result_id for fillet"
            try:
                fillet_props_json = adapter.execute_command(
                    "get_mass_properties", object_name=result_id)
                fillet_props = json.loads(
                    fillet_props_json).get("properties", {})
            except (json.JSONDecodeError, TypeError):
                fillet_props = None
            return ParameterVerifier.verify_fillet_parameters(args, fillet_props)

        elif tool == "chamfer":
            if not result_id:
                return VerificationResult.UNKNOWN, "no result_id for chamfer"
            try:
                chamfer_props_json = adapter.execute_command(
                    "get_mass_properties", object_name=result_id)
                chamfer_props = json.loads(
                    chamfer_props_json).get("properties", {})
            except (json.JSONDecodeError, TypeError):
                chamfer_props = None
            return ParameterVerifier.verify_chamfer_parameters(args, chamfer_props)

        elif tool in ("pattern_linear", "pattern_circular"):
            if not result_id:
                return VerificationResult.UNKNOWN, f"no result_id for {tool}"
            try:
                pattern_props_json = adapter.execute_command(
                    "get_mass_properties", object_name=result_id)
                pattern_props = json.loads(
                    pattern_props_json).get("properties", {})
            except (json.JSONDecodeError, TypeError):
                pattern_props = None
            # Try to get target faces for additional evidence
            target_faces_json = None
            if target_id:
                try:
                    target_faces_json = adapter.execute_command(
                        "get_faces", object_name=target_id)
                except Exception:
                    pass
            return ParameterVerifier.verify_pattern_count(args, pattern_props, target_faces_json)

        elif tool == "boolean":
            if not result_id or not target_id:
                return VerificationResult.UNKNOWN, "missing result_id or target_id for boolean"
            base_mass_json = adapter.execute_command(
                "get_mass_properties", object_name=target_id)
            result_mass_json = adapter.execute_command(
                "get_mass_properties", object_name=result_id)
            return ParameterVerifier.verify_boolean_operation(args, base_mass_json, result_mass_json)

        else:
            return VerificationResult.UNKNOWN, f"no parameter verifier for tool: {tool}"


def check_geometry(state_objects: list) -> List[str]:
    """Inspect the CAD state for signs of degenerate geometry using authoritative
    kernel-provided fields (shape_is_valid, shape_is_null, shape_volume, shape_type).

    Args:
        state_objects: Parsed CAD state (a list of object dicts). Each object
            should carry kernel-provided fields:
            - shape_is_valid: bool (from Shape.isValid())
            - shape_is_null: bool (from Shape.isNull())
            - shape_volume: float (from Shape.Volume)
            - shape_type: str (from Shape.ShapeType)

    Returns:
        A list of human-readable error strings describing every degenerate
        object found. Returns an empty list if all geometry is valid.

    Behavior:
        - valid geometry (shape_is_valid=True, shape_is_null=False, volume>0) -> no error
        - invalid geometry (shape_is_valid=False) -> error
        - null shape (shape_is_null=True) -> error
        - zero/negative volume -> error
        - missing object (not in state_objects) -> not checked here (caller handles)
        - unavailable geometry (shape_is_valid=None) -> error with "unavailable" note
    """
    errors: List[str] = []

    if not isinstance(state_objects, list):
        return errors

    for obj in state_objects:
        if not isinstance(obj, dict):
            continue

        obj_name = obj.get("id") or obj.get(
            "label") or obj.get("name") or "unknown"

        # Authoritative kernel-provided fields (BIP 4.3.3)
        shape_is_valid = obj.get("shape_is_valid")
        shape_is_null = obj.get("shape_is_null")
        shape_volume = obj.get("shape_volume")
        shape_type = obj.get("shape_type")

        # 1. Unavailable geometry state - kernel couldn't provide validity info
        if shape_is_valid is None:
            errors.append(
                f"Object '{obj_name}': geometry verification unavailable "
                f"(kernel did not provide shape_is_valid)."
            )
            continue  # Cannot make further determinations without kernel data

        # 2. Explicit invalid shape from kernel
        if shape_is_valid is False:
            errors.append(
                f"Object '{obj_name}': kernel reports invalid shape "
                f"(Shape.isValid() == False)."
            )

        # 3. Null shape from kernel
        if shape_is_null is True:
            errors.append(
                f"Object '{obj_name}': kernel reports null shape "
                f"(Shape.isNull() == True)."
            )

        # 4. Volume check - zero or negative volume is degenerate
        if shape_volume is not None:
            try:
                vol = float(shape_volume)
            except (TypeError, ValueError):
                vol = None
            if vol is not None and vol <= 0.0:
                errors.append(
                    f"Object '{obj_name}' has zero/negative volume "
                    f"({vol}) (degenerate)."
                )
        else:
            # Volume unavailable from kernel
            errors.append(
                f"Object '{obj_name}': volume unavailable from kernel."
            )

        # 5. Shape type as supplementary info (for debugging/visibility)
        if isinstance(shape_type, str) and shape_type.lower() in (
            "null", "invalid", "none", "empty"
        ):
            errors.append(
                f"Object '{obj_name}' has shape_type '{shape_type}' (degenerate)."
            )

    return errors
