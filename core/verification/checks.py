"""
Agnostic Geometric Verification Engine

Provides CAD-agnostic, mathematically-grounded geometric verification methods.
These checks operate on JSON output from adapter tools (get_mass_properties, get_faces)
and contain zero CAD-system-specific terminology or imports.
"""

import json
from typing import List, Dict, Any


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
