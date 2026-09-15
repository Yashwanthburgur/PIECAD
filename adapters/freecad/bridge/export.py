"""PieCAD FreeCAD Bridge - Export Module.

Contains implementation for exporting the visible assembly to STEP or STL files.
"""

import FreeCAD as App
import FreeCADGui as Gui


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
