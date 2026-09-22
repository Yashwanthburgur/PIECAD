"""PieCAD FreeCAD Bridge - Features Module.

Contains implementations for edge-based features (Fillet, Chamfer).
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part

from ._common import _active_doc, _sync


def _impl_edit_feature(id: str, target_id: str, parameters: dict):
    """Modify the parametric properties of an existing CAD feature.

    Supports regular FreeCAD properties (e.g., Length, Radius, Height) as
    well as the special coordinate keys 'x', 'y', and 'z', which are applied
    to the object's Placement.Base (absolute position) instead of being
    guessed as nonexistent attributes.

    Args:
        id: Unique ID for this edit operation
        target_id: The ID of the object to modify (e.g., 'box1')
        parameters: Dictionary of property names and their new float values
                   (e.g., {'Length': 120.0, 'x': 40.0})

    Returns:
        Success message after sync.
    """
    doc = _active_doc()
    obj = doc.getObject(target_id)

    if not obj:
        raise RuntimeError(f"Target object '{target_id}' not found.")

    # Snapshot the current placement so x/y/z keys can patch it in place.
    placement = obj.Placement
    base = placement.Base
    placement_changed = False

    for key, value in parameters.items():
        if key == 'x':
            base.x = float(value)
            placement_changed = True
        elif key == 'y':
            base.y = float(value)
            placement_changed = True
        elif key == 'z':
            base.z = float(value)
            placement_changed = True
        elif hasattr(obj, key):
            try:
                setattr(obj, key, float(value))
            except Exception as e:
                raise RuntimeError(
                    f"Failed to set parameter '{key}' on {target_id}: {e}")
        else:
            raise RuntimeError(
                f"Object '{target_id}' does not have a parameter named '{key}'.")

    if placement_changed:
        # Explicitly write the vector back: FreeCAD may hand out value-copies
        # of Placement/Vector objects, so mutating `base` in isolation is not
        # guaranteed to propagate to `placement`.
        placement.Base = base
        obj.Placement = placement

    return _sync(doc)


def _impl_fillet(id: str, target_id: str, edge_refs: list, radius: float,
                 topology_version: int = None):
    """Apply a fillet to specific edges of an object.

    Args:
        id: Unique ID for the fillet result object
        target_id: Name of the target object to fillet
        edge_refs: List of opaque pointer strings, format "ObjectName_edge_N" (1-based index)
        radius: Fillet radius (must be > 0)
        topology_version: Optional topology version for stale reference detection (BIP 4.3.4)
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

    # BIP 4.3.4: Validate topology version if provided
    if topology_version is not None:
        import hashlib
        # Compute current topology version from edge count + lengths
        edges_data = []
        for i, edge in enumerate(target.Shape.Edges):
            edges_data.append({"length": round(float(edge.Length), 3)})
        version_data = f"{len(edges_data)}:{sum(e['length'] for e in edges_data)}".encode(
        )
        current_version = int(hashlib.md5(version_data).hexdigest()[:8], 16)
        if current_version != topology_version:
            raise RuntimeError(
                f"Topology references for '{target_id}' are stale (expected version {topology_version}, "
                f"current version {current_version}). You must call get_edges again to get updated references."
            )

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


def _impl_chamfer(id: str, target_id: str, edge_refs: list, size: float,
                  topology_version: int = None):
    """Apply a chamfer to specific edges of an object.

    Args:
        id: Unique ID for the chamfer result object
        target_id: Name of the target object to chamfer
        edge_refs: List of opaque pointer strings, format "ObjectName_edge_N" (1-based index)
        size: Chamfer distance (must be > 0)
        topology_version: Optional topology version for stale reference detection (BIP 4.3.4)
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

    # BIP 4.3.4: Validate topology version if provided
    if topology_version is not None:
        import hashlib
        # Compute current topology version from edge count + lengths
        edges_data = []
        for i, edge in enumerate(target.Shape.Edges):
            edges_data.append({"length": round(float(edge.Length), 3)})
        version_data = f"{len(edges_data)}:{sum(e['length'] for e in edges_data)}".encode(
        )
        current_version = int(hashlib.md5(version_data).hexdigest()[:8], 16)
        if current_version != topology_version:
            raise RuntimeError(
                f"Topology references for '{target_id}' are stale (expected version {topology_version}, "
                f"current version {current_version}). You must call get_edges again to get updated references."
            )

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


def _impl_shell(id, target_id, face_refs, thickness):
    """Hollow out a solid into a thin-walled container, removing the given faces.

    Uses the native OpenCASCADE B-Rep `makeThickness` operation with an
    automatic normal-sign fallback for robustness.
    """
    doc = App.ActiveDocument
    target_obj = doc.getObject(target_id)
    if not target_obj:
        raise RuntimeError(f"Target object {target_id} not found")

    shape = target_obj.Shape
    if not hasattr(shape, "Faces") or len(shape.Faces) == 0:
        raise RuntimeError(f"Target object {target_id} has no valid geometry")

    # Map face_refs (e.g. 'box1_face_6', 'Face6', or integer '6') to Part.Face objects
    faces_to_remove = []
    for f_ref in face_refs:
        f_str = str(f_ref).strip()
        idx = None
        if "_" in f_str:
            parts = f_str.split("_")
            if parts[-1].isdigit():
                idx = int(parts[-1]) - 1
        elif f_str.lower().startswith("face"):
            digits = "".join(filter(str.isdigit, f_str))
            if digits:
                idx = int(digits) - 1
        elif f_str.isdigit():
            idx = int(f_str) - 1

        if idx is not None and 0 <= idx < len(shape.Faces):
            faces_to_remove.append(shape.Faces[idx])

    thick_val = float(thickness)

    # Execute OCC B-Rep makeThickness with automatic normal fallback
    try:
        thick_shape = shape.makeThickness(faces_to_remove, thick_val, 1e-3)
    except Exception as err1:
        try:
            thick_shape = shape.makeThickness(
                faces_to_remove, -thick_val, 1e-3)
        except Exception as err2:
            raise RuntimeError(
                f"B-Rep makeThickness failed ({thick_val}mm): {err1} | Fallback failed: {err2}")

    shell_obj = doc.addObject("Part::Feature", id)
    shell_obj.Shape = thick_shape

    target_obj.ViewObject.Visibility = False
    doc.recompute()
    return _sync(doc)
