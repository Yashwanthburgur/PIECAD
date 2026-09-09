"""PieCAD FreeCAD Bridge - Features Module.

Contains implementations for edge-based features (Fillet, Chamfer).
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


def _impl_fillet(id: str, target_id: str, edge_refs: list, radius: float):
    """Apply a fillet to specific edges of an object.

    Args:
        id: Unique ID for the fillet result object
        target_id: Name of the target object to fillet
        edge_refs: List of opaque pointer strings, format "ObjectName_edge_N" (1-based index)
        radius: Fillet radius (must be > 0)
    """
    if radius <= 0:
        raise ValueError(f"Fillet radius must be > 0, got {radius}")

    if not edge_refs:
        raise ValueError("edge_refs list cannot be empty")

    doc = _active_doc()
    target = doc.getObject(target_id)
    if target is None:
        raise ValueError(f"Target object not found: {target_id}")

    if not hasattr(target, "Shape") or target.Shape is None:
        raise ValueError(f"Target object has no Shape: {target_id}")

    # Parse all edge_refs and build the FreeCAD edges list
    freecad_edges = []
    for edge_ref in edge_refs:
        if "_edge_" not in edge_ref:
            raise ValueError(
                f"Invalid edge_ref format: {edge_ref}. Expected 'ObjectName_edge_N'")

        parts = edge_ref.split("_edge_")
        if len(parts) != 2:
            raise ValueError(
                f"Invalid edge_ref format: {edge_ref}. Expected 'ObjectName_edge_N'")

        # Verify the target object matches
        if parts[0] != target_id:
            raise ValueError(
                f"Edge ref object '{parts[0]}' does not match target_id '{target_id}'")

        try:
            # FreeCAD uses 1-based edge indices
            extracted_index = int(parts[1])
        except ValueError:
            raise ValueError(f"Invalid edge index in edge_ref: {edge_ref}")

        # Part::Fillet.Edges expects tuples of (1-based_index, radius1, radius2)
        freecad_edges.append((extracted_index, float(radius), float(radius)))

    # Create fillet feature
    new_obj = doc.addObject("Part::Fillet", id)
    new_obj.Base = target
    new_obj.Edges = freecad_edges
    # Note: Part::Fillet does not have a .Radius property in FreeCAD 1.0
    # Radii are specified per-edge in the Edges list

    # Hide the original object since it's consumed
    try:
        target.ViewObject.Visibility = False
    except Exception:
        pass

    _sync(doc)
    return f"Successfully created fillet '{id}' on {len(edge_refs)} edge(s) of '{target_id}' with radius {radius}."


def _impl_chamfer(id: str, target_id: str, edge_refs: list, size: float):
    """Apply a chamfer to specific edges of an object.

    Args:
        id: Unique ID for the chamfer result object
        target_id: Name of the target object to chamfer
        edge_refs: List of opaque pointer strings, format "ObjectName_edge_N" (1-based index)
        size: Chamfer distance (must be > 0)
    """
    if size <= 0:
        raise ValueError(f"Chamfer size must be > 0, got {size}")

    if not edge_refs:
        raise ValueError("edge_refs list cannot be empty")

    doc = _active_doc()
    target = doc.getObject(target_id)
    if target is None:
        raise ValueError(f"Target object not found: {target_id}")

    if not hasattr(target, "Shape") or target.Shape is None:
        raise ValueError(f"Target object has no Shape: {target_id}")

    # Parse all edge_refs and build the FreeCAD edges list
    freecad_edges = []
    for edge_ref in edge_refs:
        if "_edge_" not in edge_ref:
            raise ValueError(
                f"Invalid edge_ref format: {edge_ref}. Expected 'ObjectName_edge_N'")

        parts = edge_ref.split("_edge_")
        if len(parts) != 2:
            raise ValueError(
                f"Invalid edge_ref format: {edge_ref}. Expected 'ObjectName_edge_N'")

        # Verify the target object matches
        if parts[0] != target_id:
            raise ValueError(
                f"Edge ref object '{parts[0]}' does not match target_id '{target_id}'")

        try:
            # FreeCAD uses 1-based edge indices
            extracted_index = int(parts[1])
        except ValueError:
            raise ValueError(f"Invalid edge index in edge_ref: {edge_ref}")

        # Part::Chamfer.Edges expects tuples of (1-based_index, distance1, distance2)
        freecad_edges.append((extracted_index, float(size), float(size)))

    # Create chamfer feature
    new_obj = doc.addObject("Part::Chamfer", id)
    new_obj.Base = target
    new_obj.Edges = freecad_edges
    # Note: Part::Chamfer does not have a .Size property in FreeCAD 1.0
    # Distances are specified per-edge in the Edges list

    # Hide the original object since it's consumed
    try:
        target.ViewObject.Visibility = False
    except Exception:
        pass

    _sync(doc)
    return f"Successfully created chamfer '{id}' on {len(edge_refs)} edge(s) of '{target_id}' with size {size}."
