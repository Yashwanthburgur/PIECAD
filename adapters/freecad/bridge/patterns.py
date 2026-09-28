"""PieCAD FreeCAD Bridge - Patterns Module.

Contains implementations for patterning features (linear and circular),
ported from the former bridge monolith.
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part

from ._common import (
    _active_doc, _finish, _op_fingerprint, _op_stamp, _op_existing_matches,
    _transaction,
)


def _impl_pattern_linear(id: str, target_id: str, direction: dict, distance: float, count: int):
    """Create a linear pattern of a target object.
    Args:
        id: Unique ID for the pattern result object
        target_id: Name of the target object to pattern
        direction: Dict with x, y, z components of the direction vector
        distance: Distance between copies (step distance)
        count: Number of copies (including original)
    """
    # Cast inputs to safe types
    c_count = int(count)
    c_distance = float(distance)

    doc = _active_doc()
    target_obj = doc.getObject(target_id)
    if not target_obj:
        raise RuntimeError(f"Target object {target_id} not found")

    # Operation-identity idempotency (BIP 5.3-hardening): converge only when
    # the existing object encodes the SAME logical operation (same target,
    # direction, distance, count). An id collision with different parameters
    # must NOT silently reuse/overwrite an unrelated object.
    fingerprint = _op_fingerprint("pattern_linear", {
        "target_id": target_id, "direction": direction,
        "distance": c_distance, "count": c_count,
    })
    exists, matches, existing_pattern = _op_existing_matches(
        doc, id, fingerprint)
    if exists:
        if matches:
            _finish(existing_pattern)
            return (f"Linear pattern '{id}' of '{target_id}' already existed "
                    f"with identical parameters; reusing it (idempotent retry).")
        raise RuntimeError(
            f"Object '{id}' already exists but represents a DIFFERENT linear "
            f"pattern. Refusing to silently overwrite it.")

    # Ensure direction components are floats
    dir_vec = App.Vector(
        float(direction.get("x", 0)),
        float(direction.get("y", 0)),
        float(direction.get("z", 0))
    )
    if dir_vec.Length == 0:
        raise ValueError("Direction vector cannot be zero")

    # Normalize to get unit direction
    unit_dir = dir_vec.normalize()

    shapes = []
    for i in range(c_count):
        shape_copy = target_obj.Shape.copy()
        # Translate by i * step in the direction
        translation = unit_dir.multiply(c_distance * i)
        shape_copy.translate(translation)
        shapes.append(shape_copy)

    # Fuse all shapes together
    if len(shapes) > 1:
        final_shape = shapes[0].multiFuse(shapes[1:])
    else:
        final_shape = shapes[0]

    with _transaction(doc, f"pattern_linear:{id}"):
        pattern_obj = doc.addObject("Part::Feature", id)
        pattern_obj.Shape = final_shape
        _op_stamp(pattern_obj, fingerprint)

        # Hide the original object
        target_obj.ViewObject.Visibility = False

        # Make the new feature visible as the current design tip
        try:
            if hasattr(pattern_obj, "ViewObject") and pattern_obj.ViewObject:
                pattern_obj.ViewObject.Visibility = True
        except Exception:
            pass

        return _finish(pattern_obj)


def _impl_pattern_circular(id: str, target_id: str, axis_origin: dict, axis_direction: dict, angle: float, count: int):
    """Create a circular pattern of a target object around an axis.
    Args:
        id: Unique ID for the pattern result object
        target_id: Name of the target object to pattern
        axis_origin: Dict with x, y, z for a point on the axis
        axis_direction: Dict with x, y, z for the axis direction vector
        angle: Total angle to cover in degrees (e.g., 360 for full circle)
        count: Number of copies (including original)
    """
    # Cast inputs to safe types
    c_count = int(count)
    c_angle = float(angle)

    doc = _active_doc()
    target_obj = doc.getObject(target_id)
    if not target_obj:
        raise RuntimeError(f"Target object {target_id} not found")

    # Operation-identity idempotency (BIP 5.4-hardening): see linear pattern.
    fingerprint = _op_fingerprint("pattern_circular", {
        "target_id": target_id, "axis_origin": axis_origin,
        "axis_direction": axis_direction, "angle": c_angle, "count": c_count,
    })
    exists, matches, existing_pattern = _op_existing_matches(
        doc, id, fingerprint)
    if exists:
        if matches:
            _finish(existing_pattern)
            return (f"Circular pattern '{id}' of '{target_id}' already existed "
                    f"with identical parameters; reusing it (idempotent retry).")
        raise RuntimeError(
            f"Object '{id}' already exists but represents a DIFFERENT circular "
            f"pattern. Refusing to silently overwrite it.")

    # Ensure axis_origin and axis_direction components are floats
    center = App.Vector(
        float(axis_origin.get("x", 0)),
        float(axis_origin.get("y", 0)),
        float(axis_origin.get("z", 0))
    )
    axis = App.Vector(
        float(axis_direction.get("x", 0)),
        float(axis_direction.get("y", 0)),
        float(axis_direction.get("z", 0))
    )
    if axis.Length == 0:
        raise ValueError("Axis direction vector cannot be zero")

    # Normalize axis (though rotate doesn't require unit vector, we do it for consistency)
    axis = axis.normalize()

    # Angle per step in degrees
    if c_count > 1:
        step_angle = c_angle / c_count
    else:
        step_angle = 0.0

    shapes = []
    for i in range(c_count):
        shape_copy = target_obj.Shape.copy()
        # FreeCAD's native rotate method handles rotation around a point natively in degrees
        shape_copy.rotate(center, axis, step_angle * i)
        shapes.append(shape_copy)

    # Fuse all shapes together
    if len(shapes) > 1:
        final_shape = shapes[0].multiFuse(shapes[1:])
    else:
        final_shape = shapes[0]

    with _transaction(doc, f"pattern_circular:{id}"):
        pattern_obj = doc.addObject("Part::Feature", id)
        pattern_obj.Shape = final_shape
        _op_stamp(pattern_obj, fingerprint)

        # Hide the original object
        target_obj.ViewObject.Visibility = False

        # Make the new feature visible as the current design tip
        try:
            if hasattr(pattern_obj, "ViewObject") and pattern_obj.ViewObject:
                pattern_obj.ViewObject.Visibility = True
        except Exception:
            pass

        return _finish(pattern_obj)
