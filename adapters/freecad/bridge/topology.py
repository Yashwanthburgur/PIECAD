"""PieCAD FreeCAD Bridge - Topology Module.

Contains implementations for querying document state and topological information
(faces, edges) from FreeCAD objects.
"""

import json
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


def _impl_get_state():
    """Get state of all objects in the active document as a hierarchical DAG.

    Runs on the main thread via the QTimer queue system.
    Returns a JSON string with object id, label, type, visibility, parent/child relationships, and properties.
    """
    doc = _active_doc()
    objects_state = []
    for obj in doc.Objects:
        # Build base object info
        obj_info = {
            "id": obj.Name,
            "label": getattr(obj, "Label", obj.Name),
            "type": obj.TypeId,
            "visible": True,  # default, will be overridden if ViewObject exists
            "parents": [p.Name for p in getattr(obj, "InList", [])],
            "children": [c.Name for c in getattr(obj, "OutList", [])],
            "properties": {}
        }

        # Safely extract visibility from ViewObject
        if hasattr(obj, "ViewObject") and obj.ViewObject:
            try:
                obj_info["visible"] = bool(obj.ViewObject.Visibility)
            except Exception:
                obj_info["visible"] = True  # default to True on error

        # Extract numeric dimensions into properties dict
        for param in ["Length", "Width", "Height", "Radius"]:
            if hasattr(obj, param):
                try:
                    obj_info["properties"][param] = float(getattr(obj, param))
                except (TypeError, ValueError, AttributeError):
                    pass  # Skip non-numeric or inaccessible properties

        objects_state.append(obj_info)

    return json.dumps(objects_state)


def _impl_get_faces(obj_name: str):
    """Query the B-rep faces of an existing object.

    Returns a list of face dicts with:
    - face_id (opaque pointer string)
    - face_index (1-based index)
    - center (CenterOfMass as dict with x, y, z; rounded to 3 decimals)
    - area (float, rounded to 3 decimals)
    """
    doc = _active_doc()
    obj = doc.getObject(obj_name)
    if obj is None:
        raise ValueError(f"Object not found: {obj_name}")

    if not hasattr(obj, "Shape") or obj.Shape is None:
        return []

    faces_data = []
    for i, face in enumerate(obj.Shape.Faces):
        faces_data.append({
            "face_id": f"{obj_name}_face_{i+1}",
            "face_index": i + 1,
            "center": {
                "x": round(float(face.CenterOfMass.x), 3),
                "y": round(float(face.CenterOfMass.y), 3),
                "z": round(float(face.CenterOfMass.z), 3)
            },
            "area": round(float(face.Area), 3)
        })

    return faces_data


def _impl_get_edges(obj_name: str):
    """Query the B-rep edges of an existing object.

    Returns a list of edge dicts with:
    - edge_id (opaque pointer string)
    - edge_index (1-based index)
    - center (CenterOfMass as dict with x, y, z; rounded to 3 decimals)
    - length (float, rounded to 3 decimals)
    """
    doc = _active_doc()
    obj = doc.getObject(obj_name)
    if obj is None:
        raise ValueError(f"Object not found: {obj_name}")

    if not hasattr(obj, "Shape") or obj.Shape is None:
        return []

    edges_data = []
    for i, edge in enumerate(obj.Shape.Edges):
        edges_data.append({
            "edge_id": f"{obj_name}_edge_{i+1}",
            "edge_index": i + 1,
            "center": {
                "x": round(float(edge.CenterOfMass.x), 3),
                "y": round(float(edge.CenterOfMass.y), 3),
                "z": round(float(edge.CenterOfMass.z), 3)
            },
            "length": round(float(edge.Length), 3)
        })

    return edges_data
