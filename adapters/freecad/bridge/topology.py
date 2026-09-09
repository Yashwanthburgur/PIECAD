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


def _impl_get_faces(object_name: str):
    """Query the B-rep faces of an existing object.

    Returns a list of face dicts with:
    - face_index (1-based index)
    - center (CenterOfMass as dict with x, y, z)
    - area (float)
    - face_id (opaque pointer string)
    """
    doc = _active_doc()
    obj = doc.getObject(object_name)
    if obj is None:
        raise ValueError(f"Object not found: {object_name}")

    if not hasattr(obj, "Shape") or obj.Shape is None:
        return []

    faces = []
    for face_index, face in enumerate(obj.Shape.Faces, start=1):
        center = face.CenterOfMass
        face_dict = {
            "face_index": face_index,
            "center": {"x": center.x, "y": center.y, "z": center.z},
            "area": face.Area,
            "face_id": f"{object_name}_face_{face_index}",
        }
        faces.append(face_dict)

    return faces


def _impl_get_edges(object_name: str):
    """Query the B-rep edges of an existing object.

    Returns a list of edge dicts with:
    - edge_index (1-based index)
    - center (CenterOfMass as dict with x, y, z)
    - length (float)
    - edge_id (opaque pointer string)
    """
    doc = _active_doc()
    obj = doc.getObject(object_name)
    if obj is None:
        raise ValueError(f"Object not found: {object_name}")

    if not hasattr(obj, "Shape") or obj.Shape is None:
        return []

    edges = []
    for edge_index, edge in enumerate(obj.Shape.Edges, start=1):
        center = edge.CenterOfMass
        edge_dict = {
            "edge_index": edge_index,
            "center": {"x": center.x, "y": center.y, "z": center.z},
            "length": edge.Length,
            "edge_id": f"{object_name}_edge_{edge_index}",
        }
        edges.append(edge_dict)

    return edges
