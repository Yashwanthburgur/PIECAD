"""PieCAD FreeCAD Bridge - Topology Module.

Contains implementations for querying document state and topological information
(faces, edges) from FreeCAD objects.
"""

import json
import FreeCAD as App
import FreeCADGui as Gui

from ._common import _active_doc


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


def _impl_get_mass_properties(id, object_name):
    """Calculate engineering mass properties (volume, center of mass, bounding
    box) for a specific solid body. All floats are rounded to 3 decimals to
    keep LLM context small."""
    doc = App.ActiveDocument
    obj = doc.getObject(object_name)
    if not obj or not hasattr(obj, "Shape") or obj.Shape.isNull():
        raise RuntimeError(
            f"Object {object_name} not found or has no valid shape.")

    shape = obj.Shape
    return {
        "status": "success",
        "volume": round(float(shape.Volume), 3),
        "center_of_mass": {
            "x": round(float(shape.CenterOfMass.x), 3),
            "y": round(float(shape.CenterOfMass.y), 3),
            "z": round(float(shape.CenterOfMass.z), 3)
        },
        "bounding_box": {
            "XMin": round(float(shape.BoundBox.XMin), 3), "XMax": round(float(shape.BoundBox.XMax), 3),
            "YMin": round(float(shape.BoundBox.YMin), 3), "YMax": round(float(shape.BoundBox.YMax), 3),
            "ZMin": round(float(shape.BoundBox.ZMin), 3), "ZMax": round(float(shape.BoundBox.ZMax), 3)
        }
    }


def _impl_get_bom(id):
    """Generate a Bill of Materials: all visible, distinct solid parts in the
    active document, each with its name and volume (rounded to 3 decimals)."""
    doc = App.ActiveDocument
    bom = []
    # Iterate through objects. Count only visible objects with a valid solid shape.
    for obj in doc.Objects:
        if hasattr(obj, "Shape") and not obj.Shape.isNull() and hasattr(obj, "ViewObject"):
            if obj.ViewObject and obj.ViewObject.Visibility:
                bom.append({
                    "name": obj.Name,
                    "volume": round(float(obj.Shape.Volume), 3)
                })
    return {"status": "success", "parts": bom}
