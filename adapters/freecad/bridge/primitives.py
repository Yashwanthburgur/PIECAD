"""PieCAD FreeCAD Bridge - Primitives Module.

Contains implementations for creating primitive solids (Box, Cylinder).
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part

from ._common import _active_doc, _finish, _impl_set_visible


def _impl_create_box(length, width, height, object_name="Box"):
    """Create a box primitive.

    Idempotent by requested object_name (BIP 5.2): if a box with this ID
    already exists it means an earlier attempt actually created it but its
    response was lost and the agent retried the same operation. Reuse and
    update that object instead of spawning a duplicate (Box001, ...).
    """
    doc = _active_doc()
    existing = doc.getObject(object_name)
    if existing is not None:
        existing.Length = float(length)
        existing.Width = float(width)
        existing.Height = float(height)
        _finish(doc)
        _impl_set_visible(existing, True)
        return (f"Box '{existing.Name}' already existed; updated to "
                f"{length}x{width}x{height}.")
    obj = doc.addObject("Part::Box", object_name)
    obj.Length = float(length)
    obj.Width = float(width)
    obj.Height = float(height)
    _finish(doc)
    _impl_set_visible(obj, True)
    return f"Successfully created Box {length}x{width}x{height} as '{obj.Name}'."


def _impl_create_cylinder(radius, height, object_name="Cylinder"):
    """Create a cylinder primitive.

    Idempotent by requested object_name (BIP 5.2): reuse an existing cylinder
    with the same ID rather than creating a duplicate on retry.
    """
    doc = _active_doc()
    existing = doc.getObject(object_name)
    if existing is not None:
        existing.Radius = float(radius)
        existing.Height = float(height)
        _finish(doc)
        _impl_set_visible(existing, True)
        return (f"Cylinder '{existing.Name}' already existed; updated to "
                f"r={radius} h={height}.")
    obj = doc.addObject("Part::Cylinder", object_name)
    obj.Radius = float(radius)
    obj.Height = float(height)
    _finish(doc)
    _impl_set_visible(obj, True)
    return f"Successfully created Cylinder r={radius} h={height} as '{obj.Name}'."
