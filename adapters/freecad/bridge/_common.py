"""Common helper functions for the FreeCAD Bridge package.

DRY (Don't Repeat Yourself) - consolidated from topology.py, boolean.py,
features.py, primitives.py, sketch.py, assembly.py, patterns.py, export.py.
"""

import FreeCAD as App
import FreeCADGui as Gui


def _active_doc():
    """Get or create the active FreeCAD document."""
    doc = App.ActiveDocument
    if not doc:
        raise RuntimeError("No active FreeCAD document.")
    return doc


def _sync(doc=None):
    """Sync the document after geometry changes: recompute and fit view.
    Raises RuntimeError if recompute fails or if any object becomes invalid.
    """
    if doc is None:
        doc = App.ActiveDocument
    if not doc:
        return {"status": "success"}

    try:
        doc.recompute()
    except Exception as e:
        raise RuntimeError(f"FreeCAD Kernel Recompute Failed: {str(e)}")

    # Check for invalid objects after recompute
    for obj in doc.Objects:
        # Check State attribute for Invalid
        if hasattr(obj, "State"):
            state = getattr(obj, "State")
            if isinstance(state, (list, tuple)) and any("Invalid" in str(s) for s in state):
                raise RuntimeError(
                    f"FreeCAD Kernel Invalid Geometry: Object '{obj.Name}' failed to compute (State: {state}).")
            elif isinstance(state, str) and "Invalid" in state:
                raise RuntimeError(
                    f"FreeCAD Kernel Invalid Geometry: Object '{obj.Name}' failed to compute (State: {state}).")
        # Check isValid method/attribute
        if hasattr(obj, "isValid"):
            is_valid = obj.isValid
            if callable(is_valid):
                if not is_valid():
                    raise RuntimeError(
                        f"FreeCAD Kernel Invalid Geometry: Object '{obj.Name}' failed to compute (State: {getattr(obj, 'State', 'Unknown')}).")
            else:
                # If it's an attribute
                if not is_valid:
                    raise RuntimeError(
                        f"FreeCAD Kernel Invalid Geometry: Object '{obj.Name}' failed to compute (State: {getattr(obj, 'State', 'Unknown')}).")

    # Only update view if recompute succeeded and no invalid objects
    try:
        Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass

    return {"status": "success"}


def _finish(obj, doc=None):
    if doc is None:
        doc = App.ActiveDocument
    if doc:
        doc.recompute()
    return {"status": "success", "id": obj.Name}


def _impl_set_visible(obj, visible=True):
    if hasattr(obj, "ViewObject") and obj.ViewObject:
        obj.ViewObject.Visibility = visible
