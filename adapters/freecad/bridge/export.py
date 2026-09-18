"""PieCAD FreeCAD Bridge - Export Module.

Contains implementation for exporting the visible assembly to STEP or STL files.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui

from ._common import _active_doc


def _impl_export_model(id: str, format_type: str, filepath: str):
    """Export all visible solid objects to a STEP or STL file.

    Args:
        id: Unique ID for this export operation
        format_type: 'step' or 'stl'
        filepath: Absolute path to the output file

    Returns:
        Success message with the filepath.
    """
    doc = _active_doc()

    # Filter objects: must have valid Shape (not null) AND be visible
    visible_objects = []
    for obj in doc.Objects:
        if hasattr(obj, "Shape") and not obj.Shape.isNull() and hasattr(obj, "ViewObject"):
            if obj.ViewObject and obj.ViewObject.Visibility:
                visible_objects.append(obj)

    if not visible_objects:
        raise RuntimeError("No visible solid objects found to export.")

    fmt = format_type.lower()

    if fmt == "stl":
        import Mesh
        Mesh.export(visible_objects, filepath)
    elif fmt == "step":
        import Import
        Import.export(visible_objects, filepath)
    else:
        raise RuntimeError(
            f"Unsupported export format: {format_type}. Use 'step' or 'stl'.")

    return f"Successfully exported {len(visible_objects)} object(s) to {filepath} (format: {fmt.upper()})."


def export_current_state(filepath: str, format: str = "glb"):
    """Export all visible solid objects in the active document to GLB/glTF.

    Tries FreeCAD's native glTF/GLB exporter first. If the running FreeCAD
    version lacks it, falls back to exporting a standard Wavefront .obj file
    (same base name with a .obj extension).

    Args:
        filepath: Absolute path to the output file (e.g. .../piecad_state.glb).
        format: 'glb' (default) or 'gltf'.

    Returns:
        Human-readable success message containing the actually written filepath.
    """
    doc = _active_doc()

    # Gather all visible solid objects (valid non-null Shape, solid-bearing,
    # and not hidden). In headless mode there are no view providers, so every
    # valid solid is included.
    visible_objects = []
    for obj in doc.Objects:
        if not hasattr(obj, "Shape") or obj.Shape.isNull():
            continue
        try:
            if not obj.Shape.Solids:
                continue
        except Exception:
            continue
        view_obj = getattr(obj, "ViewObject", None)
        if view_obj is not None:
            # GUI mode: respect the current visibility flag.
            if getattr(view_obj, "Visibility", True):
                visible_objects.append(obj)
        else:
            # Headless mode: no view providers exist.
            visible_objects.append(obj)

    if not visible_objects:
        raise RuntimeError("No visible solid objects found to export.")

    fmt = (format or "glb").lower()
    if fmt not in ("glb", "gltf"):
        fmt = "glb"

    # 1. Try FreeCAD's native GLB/glTF export.
    try:
        import Import
        Import.export(visible_objects, filepath)
        if os.path.exists(filepath):
            return (f"Successfully exported {len(visible_objects)} object(s) "
                    f"to {filepath} (format: {fmt.upper()}).")
    except Exception:
        pass  # Older FreeCAD versions lack the glTF exporter; fall back below.

    # 2. Fallback: standard Wavefront OBJ export via FreeCAD's Mesh module.
    obj_path = os.path.splitext(filepath)[0] + ".obj"
    import Mesh
    Mesh.export(visible_objects, obj_path)
    if not os.path.exists(obj_path):
        raise RuntimeError(
            "GLB/glTF export unavailable in this FreeCAD version and the "
            f"OBJ fallback produced no file: {obj_path}")
    return (f"GLB/glTF export unavailable in this FreeCAD version; "
            f"exported {len(visible_objects)} object(s) to {obj_path} (format: OBJ).")
