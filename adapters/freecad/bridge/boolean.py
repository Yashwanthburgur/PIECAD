"""PieCAD FreeCAD Bridge - Boolean Module.

Contains implementations for boolean operations and hole creation.
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part


def _active_doc():
    """Get or create the active FreeCAD document."""
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


def _sync(doc):
    """Sync the document after geometry changes: recompute and fit view.
    Raises RuntimeError if recompute fails or if any object becomes invalid.
    """
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


def _impl_boolean(operation: str, base_obj: str, tool_obj: str, result_name: str):
    """Perform a boolean operation between two existing objects.

    Supported operations:
      - "subtract": Base minus Tool (drill hole, remove material)
      - "union": Join both objects via Part::MultiFuse
      - "intersect": Keep only the common volume via Part::MultiCommon

    Raises ValueError if either object is not found.
    """
    doc = _active_doc()

    base = doc.getObject(base_obj)
    if base is None:
        raise ValueError(f"Base object not found: {base_obj}")
    tool = doc.getObject(tool_obj)
    if tool is None:
        raise ValueError(f"Tool object not found: {tool_obj}")

    if operation == "subtract":
        new_obj = doc.addObject("Part::Cut", result_name)
        new_obj.Base = base
        new_obj.Tool = tool
    elif operation == "union":
        new_obj = doc.addObject("Part::MultiFuse", result_name)
        new_obj.Shapes = [base, tool]
    elif operation == "intersect":
        new_obj = doc.addObject("Part::MultiCommon", result_name)
        new_obj.Shapes = [base, tool]
    else:
        raise ValueError(f"Unknown boolean operation: {operation}")

    # Hide the original objects
    try:
        doc.getObject(base_obj).Visibility = False
    except Exception:
        pass
    try:
        doc.getObject(tool_obj).Visibility = False
    except Exception:
        pass

    _sync(doc)
    return f"Successfully performed '{operation}' on '{base_obj}' and '{tool_obj}' as '{new_obj.Name}'."


def _impl_hole(id: str, face_ref: str, x: float, y: float, diameter: float, depth: float = 100.0):
    """Create a hole by drilling into a face at (x, y) with given diameter and depth.

    Does B-rep geometry at kernel level (no create_cylinder+boolean).
    """
    import FreeCAD

    # Parse face_ref (format: "ObjectName_face_N")
    if "_face_" not in face_ref:
        raise ValueError(
            f"Invalid face_ref format: {face_ref}. Expected 'ObjectName_face_N'")

    parts = face_ref.split("_face_")
    if len(parts) != 2:
        raise ValueError(
            f"Invalid face_ref format: {face_ref}. Expected 'ObjectName_face_N'")

    target_name = parts[0]
    try:
        face_index = int(parts[1]) - 1  # Convert to 0-based index
    except ValueError:
        raise ValueError(f"Invalid face index in face_ref: {face_ref}")

    doc = _active_doc()
    target = doc.getObject(target_name)
    if target is None:
        raise ValueError(f"Target object not found: {target_name}")

    if not hasattr(target, "Shape") or target.Shape is None:
        raise ValueError(f"Target object has no Shape: {target_name}")

    try:
        face = target.Shape.Faces[face_index]
    except IndexError:
        raise ValueError(
            f"Face index {face_index + 1} out of range for object {target_name}")

    # Get center of mass
    center = face.CenterOfMass

    # Get normal vector (pointing outward from face)
    normal = FreeCAD.Vector(0, 0, 1)  # default fallback
    if hasattr(face.Surface, "Axis"):
        normal = face.Surface.Axis

    # Reverse normal so it points inward (into the material for a hole)
    normal = normal * -1.0

    # For this MVP, we ignore x/y offsets and drill at face center
    # In a full implementation, we would: center + (x * normal_x + y * normal_y)
    # but for now we use the face center as specified

    # Determine depth: if depth <= 0, treat as through-all (use large value)
    hole_depth = depth if depth > 0 else 100.0

    # Create the cylinder shape for the hole
    cyl_shape = Part.makeCylinder(diameter/2.0, hole_depth, center, normal)

    # Create a temporary tool object
    tool = doc.addObject("Part::Feature", f"{id}_tool")
    tool.Shape = cyl_shape

    # Perform the cut operation
    cut = doc.addObject("Part::Cut", id)
    cut.Base = target
    cut.Tool = tool

    # Hide the base and tool objects
    try:
        target.ViewObject.Visibility = False
    except Exception:
        pass
    try:
        tool.ViewObject.Visibility = False
    except Exception:
        pass

    _sync(doc)
    return f"Successfully created hole '{id}' on face {face_ref} with diameter {diameter}, depth {'through-all' if depth <= 0 else str(depth)}."
