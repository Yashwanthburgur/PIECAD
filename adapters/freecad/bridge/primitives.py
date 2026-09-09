"""PieCAD FreeCAD Bridge - Primitives Module.

Contains implementations for creating primitive solids (Box, Cylinder).
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part


def _active_doc():
    """Get or create the active FreeCAD document."""
    import FreeCAD as App
    import FreeCADGui as Gui

    doc = App.ActiveDocument
    if doc is None:
        doc = App.newDocument("PieCAD_Model")
    # Ensure the document is the GUI-active one too (so its view is shown).
    try:
        gui_doc = Gui.getDocument(doc.Name)
        if gui_doc is not None:
            Gui.setActiveDocument(doc)
    except Exception:
        pass
    return doc


def _finish(doc):
    """Finish document operation: recompute and fit view."""
    import FreeCADGui as Gui

    doc.recompute()
    try:
        Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
    return doc


def _impl_set_visible(doc, name, visible):
    """Set visibility of an object."""
    import FreeCADGui as Gui

    try:
        view = Gui.getDocument(doc.Name).getObject(name)
        if view is not None:
            view.Visibility = visible
    except Exception:
        pass
    try:
        obj = doc.getObject(name)
        if obj is not None and hasattr(obj, "Visibility"):
            obj.Visibility = visible
    except Exception:
        pass


def _impl_create_box(length, width, height, object_name="Box"):
    """Create a box primitive."""
    doc = _active_doc()
    obj = doc.addObject("Part::Box", object_name)
    obj.Length = float(length)
    obj.Width = float(width)
    obj.Height = float(height)
    _finish(doc)
    _impl_set_visible(doc, obj.Name, True)
    return f"Successfully created Box {length}x{width}x{height} as '{obj.Name}'."


def _impl_create_cylinder(radius, height, object_name="Cylinder"):
    """Create a cylinder primitive."""
    doc = _active_doc()
    obj = doc.addObject("Part::Cylinder", object_name)
    obj.Radius = float(radius)
    obj.Height = float(height)
    _finish(doc)
    _impl_set_visible(doc, obj.Name, True)
    return f"Successfully created Cylinder r={radius} h={height} as '{obj.Name}'."
