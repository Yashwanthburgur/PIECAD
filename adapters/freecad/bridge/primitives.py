"""PieCAD FreeCAD Bridge - Primitives Module.

Contains implementations for creating primitive solids (Box, Cylinder).
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part

from ._common import (
    _active_doc, _finish, _impl_set_visible,
    _op_fingerprint, _op_stamp, _op_existing_matches,
)


def _impl_create_box(length, width, height, object_name="Box"):
    """Create a box primitive.

    Operation-identity idempotency (BIP 5.2-hardening): an identical logical
    retry (same object_name, same dimensions) converges to the existing box.
    A DIFFERENT operation reusing the id is REJECTED rather than silently
    overwriting unrelated geometry.
    """
    doc = _active_doc()
    fingerprint = _op_fingerprint("box", {
        "length": float(length), "width": float(width),
        "height": float(height),
    })
    exists, matches, existing = _op_existing_matches(
        doc, object_name, fingerprint)
    if exists:
        if matches:
            _finish(doc)
            _impl_set_visible(existing, True)
            return (f"Box '{existing.Name}' already existed with identical "
                    f"parameters; reusing it (idempotent retry).")
        raise RuntimeError(
            f"Object '{object_name}' already exists but represents a DIFFERENT "
            f"box. Refusing to silently overwrite it.")
    obj = doc.addObject("Part::Box", object_name)
    obj.Length = float(length)
    obj.Width = float(width)
    obj.Height = float(height)
    _op_stamp(obj, fingerprint)
    _finish(doc)
    _impl_set_visible(obj, True)
    return f"Successfully created Box {length}x{width}x{height} as '{obj.Name}'."


def _impl_create_cylinder(radius, height, object_name="Cylinder"):
    """Create a cylinder primitive.

    Operation-identity idempotency: identical retry converges; id collision
    with different dimensions is rejected (no silent overwrite).
    """
    doc = _active_doc()
    fingerprint = _op_fingerprint("cylinder", {
        "radius": float(radius), "height": float(height),
    })
    exists, matches, existing = _op_existing_matches(
        doc, object_name, fingerprint)
    if exists:
        if matches:
            _finish(doc)
            _impl_set_visible(existing, True)
            return (f"Cylinder '{existing.Name}' already existed with identical "
                    f"parameters; reusing it (idempotent retry).")
        raise RuntimeError(
            f"Object '{object_name}' already exists but represents a DIFFERENT "
            f"cylinder. Refusing to silently overwrite it.")
    obj = doc.addObject("Part::Cylinder", object_name)
    obj.Radius = float(radius)
    obj.Height = float(height)
    _op_stamp(obj, fingerprint)
    _finish(doc)
    _impl_set_visible(obj, True)
    return f"Successfully created Cylinder r={radius} h={height} as '{obj.Name}'."
