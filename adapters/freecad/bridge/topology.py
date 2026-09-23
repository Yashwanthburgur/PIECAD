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
    Returns a JSON string with object id, label, type, visibility, parent/child
    relationships, properties, and authoritative geometry verification fields:
    - shape_is_valid: bool (from Shape.isValid())
    - shape_is_null: bool (from Shape.isNull())
    - shape_volume: float (from Shape.Volume)
    - shape_type: str (from Shape.ShapeType)
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

        # --- Authoritative geometry verification fields (BIP 4.3.3) ---
        # These come directly from the FreeCAD kernel's Shape, not derived.
        if hasattr(obj, "Shape") and obj.Shape is not None:
            shape = obj.Shape
            try:
                obj_info["shape_is_valid"] = bool(shape.isValid())
            except Exception:
                obj_info["shape_is_valid"] = None
            try:
                obj_info["shape_is_null"] = bool(shape.isNull())
            except Exception:
                obj_info["shape_is_null"] = None
            try:
                obj_info["shape_volume"] = float(shape.Volume)
            except Exception:
                obj_info["shape_volume"] = None
            try:
                obj_info["shape_type"] = str(shape.ShapeType)
            except Exception:
                obj_info["shape_type"] = None
        else:
            # No Shape attribute or Shape is None - geometry unavailable
            obj_info["shape_is_valid"] = None
            obj_info["shape_is_null"] = None
            obj_info["shape_volume"] = None
            obj_info["shape_type"] = None

        objects_state.append(obj_info)

    return json.dumps(objects_state)


def _impl_get_faces(obj_name: str):
    """Query the B-rep faces of an existing object.

    Returns a dict with:
    - faces: list of face dicts (face_id, face_index, center, area)
    - topology_version: str (deterministic hex digest for this object's faces)
    """
    doc = _active_doc()
    obj = doc.getObject(obj_name)
    if obj is None:
        raise ValueError(f"Object not found: {obj_name}")

    if not hasattr(obj, "Shape") or obj.Shape is None:
        return {"faces": [], "topology_version": "0"}

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

    # Include topology version for stale reference detection (BIP 4.3.4)
    # Use hash of face count + areas as a simple version proxy
    # In a full implementation, FreeCAD's Shape hashCode could be used
    import hashlib
    version_data = f"{len(faces_data)}:{sum(f['area'] for f in faces_data)}".encode(
    )
    topology_version = hashlib.md5(version_data).hexdigest()[:16]

    return {"faces": faces_data, "topology_version": topology_version}


def _impl_get_edges(obj_name: str):
    """Query the B-rep edges of an existing object.

    Returns a dict with:
    - edges: list of edge dicts (edge_id, edge_index, center, length)
    - topology_version: str (deterministic hex digest for this object's edges)
    """
    doc = _active_doc()
    obj = doc.getObject(obj_name)
    if obj is None:
        raise ValueError(f"Object not found: {obj_name}")

    if not hasattr(obj, "Shape") or obj.Shape is None:
        return {"edges": [], "topology_version": "0"}

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

    # Include topology version for stale reference detection (BIP 4.3.4)
    import hashlib
    version_data = f"{len(edges_data)}:{sum(e['length'] for e in edges_data)}".encode(
    )
    topology_version = hashlib.md5(version_data).hexdigest()[:16]

    return {"edges": edges_data, "topology_version": topology_version}


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


def _impl_interference_check(id: str, part_ids=None):
    """Detect physical clashes between assembly parts via pairwise B-Rep boolean
    intersection (OpenCASCADE `common()`).

    Args:
        id: Unique ID for this query (passed through for parity with other tools).
        part_ids: Optional list of object IDs to test. If None, every visible
            solid object in the active document is tested.

    Returns:
        JSON-serializable dict:
        {"status": "success", "has_clash": bool, "clash_count": int,
         "clashes": [{"part_a": ..., "part_b": ..., "clash_volume": float}]}
        A pair is flagged when the intersection volume exceeds 1e-4 mm^3,
        which filters out numerical noise from touching faces/edges.
    """
    from itertools import combinations

    TOLERANCE_MM3 = 1e-4

    doc = _active_doc()

    # --- Target selection ---
    solids = []
    if part_ids:
        for pid in part_ids:
            obj = doc.getObject(pid)
            if obj is None:
                raise RuntimeError(
                    f"Object '{pid}' not found for interference check.")
            if not hasattr(obj, "Shape") or obj.Shape is None:
                raise RuntimeError(
                    f"Object '{pid}' has no Shape to test for interference.")
            solids.append(obj)
    else:
        for obj in doc.Objects:
            # Must be a visible object with a solid B-rep shape.
            if not hasattr(obj, "Shape") or obj.Shape is None:
                continue
            view = getattr(obj, "ViewObject", None)
            if view is not None and not view.Visibility:
                continue
            try:
                if obj.Shape.ShapeType != "Solid":
                    continue
            except Exception:
                continue
            solids.append(obj)

    # --- Pairwise collision math ---
    clashes = []
    for obj_a, obj_b in combinations(solids, 2):
        try:
            common_shape = obj_a.Shape.common(obj_b.Shape)
            overlap = float(common_shape.Volume)
        except Exception:
            # OCC boolean failure on degenerate geometry: skip this pair
            # rather than aborting the whole analysis.
            continue
        if overlap > TOLERANCE_MM3:
            clashes.append({
                "part_a": getattr(obj_a, "Label", obj_a.Name),
                "part_b": getattr(obj_b, "Label", obj_b.Name),
                "clash_volume": round(overlap, 4),
            })

    return {
        "status": "success",
        "has_clash": bool(clashes),
        "clash_count": len(clashes),
        "clashes": clashes,
    }
