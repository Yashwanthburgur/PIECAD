"""PieCAD FreeCAD Bridge - Primitives Module.

Contains implementations for creating primitive solids (Box, Cylinder).
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part

from ._common import _active_doc, _finish, _impl_set_visible


def _impl_create_box(length, width, height, object_name="Box"):
    """Create a box primitive."""
    doc = _active_doc()
    obj = doc.addObject("Part::Box", object_name)
    obj.Length = float(length)
    obj.Width = float(width)
    obj.Height = float(height)
    _finish(doc)
    _impl_set_visible(obj, True)
    return f"Successfully created Box {length}x{width}x{height} as '{obj.Name}'."


def _impl_create_cylinder(radius, height, object_name="Cylinder"):
    """Create a cylinder primitive."""
    doc = _active_doc()
    obj = doc.addObject("Part::Cylinder", object_name)
    obj.Radius = float(radius)
    obj.Height = float(height)
    _finish(doc)
    _impl_set_visible(obj, True)
    return f"Successfully created Cylinder r={radius} h={height} as '{obj.Name}'."
