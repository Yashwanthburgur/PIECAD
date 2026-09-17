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
    def verify_exists(final_mass_json: str) -> bool:
        """
        Verify that a final solid exists and has positive volume.

        Args:
            final_mass_json: JSON from get_mass_properties on the final solid.

        Returns:
            True if the JSON parses and its Volume is strictly greater than 0.0,
            False otherwise (including parse errors).
        """
        try:
            data = json.loads(final_mass_json)
            volume = data.get("Volume", 0.0)
            return volume > 0.0
        except (json.JSONDecodeError, AttributeError, TypeError, KeyError):
            return False

    @staticmethod
    def verify_volume_reduction(base_mass_json: str, cut_mass_json: str) -> bool:
        """
        Verify that a boolean subtraction/hole operation actually removed material.

        Args:
            base_mass_json: JSON from get_mass_properties on the base solid (before cut).
            cut_mass_json: JSON from get_mass_properties on the resulting solid (after cut).

        Returns:
            True if volume strictly decreased, False otherwise (including parse errors).
        """
        try:
            base = json.loads(base_mass_json)
            cut = json.loads(cut_mass_json)

            base_vol = base.get("volume", 0.0)
            cut_vol = cut.get("volume", 0.0)

            # Volume must be strictly less after a cut operation
            return cut_vol < base_vol
        except (json.JSONDecodeError, KeyError, TypeError):
            return False

    @staticmethod
    def verify_face_count_increase(base_faces_json: str, op_faces_json: str) -> bool:
        """
        Verify that an operation (chamfer, fillet, patterned holes) increased face count.

        Args:
            base_faces_json: JSON from get_faces on the base solid.
            op_faces_json: JSON from get_faces on the operated solid.

        Returns:
            True if face count strictly increased, False otherwise.
        """
        try:
            base_faces = json.loads(base_faces_json)
            op_faces = json.loads(op_faces_json)

            base_count = len(base_faces) if isinstance(base_faces, list) else 0
            op_count = len(op_faces) if isinstance(op_faces, list) else 0

            return op_count > base_count
        except (json.JSONDecodeError, TypeError):
            return False

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
