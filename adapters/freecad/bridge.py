"""PieCAD FreeCAD XML-RPC Bridge (main-thread execution).

FreeCAD is NOT thread-safe: creating documents/objects and recomputing the 3D
Coin3D scene must happen on the GUI (main) thread. XML-RPC servers, however,
dispatch each request on a worker thread.

This bridge therefore uses a **two-thread design**:

1. The XML-RPC server (worker thread) receives a call, enqueues it, then BLOCKS
   waiting for the result.
2. A QTimer on the FreeCAD main thread drains the queue and actually performs
   the document/object work, then signals the waiting worker.

This keeps the synchronous XML-RPC contract while guaranteeing every FreeCAD
operation (`doc.addObject`, `doc.recompute`, `Gui.SendMsgToActiveView`)
runs on the main thread — so objects render immediately.

Usage (paste into the FreeCAD Python console):

    import sys, threading
    sys.path.insert(0, str(PROJECT_ROOT / "adapters/freecad"))
    import bridge

    bridge.install_main_thread_processor()   # MUST run on the main/console thread

    t = threading.Thread(target=lambda: bridge.start(port=9876), daemon=True)
    t.start()

    panel_path = PROJECT_ROOT / "ui/freecad_panel.py"
    with open(panel_path, encoding="utf-8") as f:
        exec(f.read)
"""

import json
import xmlrpc.server
import uuid
import threading
import queue
import os
from pathlib import Path

import Part
import FreeCADGui as Gui
import FreeCAD as App

# Import QtCore robustly across FreeCAD Qt bindings.
try:
    from PySide6 import QtCore
except ImportError:
    try:
        from PySide2 import QtCore
    except ImportError:
        from PySide import QtCore

# Dynamically resolve project root (two levels up from this file's directory)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# --------------------------------------------------------------------------- #
# Thread-safe queue and results storage for main-thread execution.
# --------------------------------------------------------------------------- #


_WORK_QUEUE: "queue.Queue[tuple]" = queue.Queue()
_RESULTS: "dict[str, tuple[str, str]]" = {}
_RESULTS_EVENTS: "dict[str, threading.Event]" = {}
_RESULTS_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# Syncer: runs recompute + ViewFit on the main thread.
# --------------------------------------------------------------------------- #


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
                    5
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


# --------------------------------------------------------------------------- #
# Actual FreeCAD implementations (runs ONLY on the main thread).
# --------------------------------------------------------------------------- #


def _active_doc():
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
    doc.recompute()
    try:
        Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
    return doc


def _impl_set_visible(doc, name, visible):
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


def _impl_clear_document():
    """Clear the active FreeCAD document by closing it and creating a new one."""
    try:
        if App.ActiveDocument:
            App.closeDocument(App.ActiveDocument.Name)
    except Exception:
        pass
    App.newDocument("Unnamed")
    return "Document cleared successfully."


# --------------------------------------------------------------------------- #
# Primitive Implementations
# --------------------------------------------------------------------------- #

def _impl_create_box(length, width, height, object_name="Box"):
    doc = _active_doc()
    obj = doc.addObject("Part::Box", object_name)
    obj.Length = float(length)
    obj.Width = float(width)
    obj.Height = float(height)
    _finish(doc)
    _impl_set_visible(doc, obj.Name, True)
    return f"Successfully created Box {length}x{width}x{height} as '{obj.Name}'."


def _impl_create_cylinder(radius, height, object_name="Cylinder"):
    doc = _active_doc()
    obj = doc.addObject("Part::Cylinder", object_name)
    obj.Radius = float(radius)
    obj.Height = float(height)
    _finish(doc)
    _impl_set_visible(doc, obj.Name, True)
    return f"Successfully created Cylinder r={radius} h={height} as '{obj.Name}'."


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


def _impl_set_param(object_name, param_name, value):
    doc = _active_doc()

    obj = doc.getObject(object_name)
    if obj is None:
        raise ValueError(f"Object not found: {object_name}")

    if not hasattr(obj, param_name):
        raise ValueError(
            f"Property '{param_name}' does not exist on object '{object_name}'."
        )

    setattr(obj, param_name, float(value))
    _finish(doc)
    return f"Successfully updated {object_name}.{param_name} to {value}."


def _impl_delete_object(target_feature_id: str):
    """Delete an object from the active document.

    Un-hides consumed features (Base, Tool, Shapes) before removing the parent
    feature so they reappear in the UI.
    """
    doc = _active_doc()
    obj = doc.getObject(target_feature_id)

    if not obj:
        return f"Error: Object '{target_feature_id}' not found in active document."

    # Un-hide consumed features so they don't vanish from the UI
    if hasattr(obj, "Base") and obj.Base:
        try:
            obj.Base.ViewObject.Visibility = True
        except Exception:
            pass
    if hasattr(obj, "Tool") and obj.Tool:
        try:
            obj.Tool.ViewObject.Visibility = True
        except Exception:
            pass
    if hasattr(obj, "Shapes") and obj.Shapes:
        for shape in obj.Shapes:
            try:
                shape.ViewObject.Visibility = True
            except Exception:
                pass

    # Now safely remove the feature
    doc.removeObject(target_feature_id)
    _sync(doc)
    return f"Successfully deleted '{target_feature_id}'."


def _impl_translate(object_name: str, x: float, y: float, z: float):
    """Translate an object to an absolute position (x, y, z).

    Runs on the main thread via the QTimer queue system.
    Returns success or error string.
    """
    doc = _active_doc()
    obj = doc.getObject(object_name)
    if obj is None:
        raise ValueError(f"Object not found: {object_name}")

    import FreeCAD
    obj.Placement.Base = FreeCAD.Vector(float(x), float(y), float(z))
    _sync(doc)
    return f"Successfully translated '{object_name}' to ({x}, {y}, {z})."


def _impl_hole(id: str, target_id: str, origin: dict, direction: dict, diameter: float, depth: float, kind: str = "simple", thread_spec: str = None):
    """Create a hole by drilling into a target object.

    Supports: simple, tapped, counterbore, countersink.
    For tapped holes with M-series thread_spec (e.g., "M6"), uses the major diameter
    as the drill diameter (tap drill diameter in practice would be smaller, but we
    use major diameter for the initial cut).
    """
    import Part

    doc = _active_doc()
    target_obj = doc.getObject(target_id)
    if not target_obj:
        raise RuntimeError(f"Target object {target_id} not found")

    c_origin = App.Vector(float(origin['x']), float(
        origin['y']), float(origin['z']))
    c_dir = App.Vector(float(direction['x']), float(
        direction['y']), float(direction['z']))

    # Machine Logic: Parse M-series threads
    eff_diameter = float(diameter)
    if kind == "tapped" and thread_spec and str(thread_spec).upper().startswith("M"):
        try:
            eff_diameter = float(
                str(thread_spec).upper().replace("M", "").strip())
        except ValueError:
            pass

    eff_radius = eff_diameter / 2.0

    # Generate the drill bit natively
    cylinder_shape = Part.makeCylinder(
        eff_radius, float(depth), c_origin, c_dir)

    tool_obj = doc.addObject("Part::Feature", f"{id}_drill")
    tool_obj.Shape = cylinder_shape

    # Execute the boolean cut
    cut_obj = doc.addObject("Part::Cut", id)
    cut_obj.Base = target_obj
    cut_obj.Tool = tool_obj

    target_obj.ViewObject.Visibility = False
    tool_obj.ViewObject.Visibility = False

    _sync(doc)
    return f"Successfully created {kind} hole '{id}' in '{target_id}' with diameter {eff_diameter}, depth {depth}."


def _impl_edit_object(object_name, properties):
    doc = _active_doc()
    try:
        obj = doc.getObject(object_name)
    except Exception:
        raise ValueError(f"Object {object_name} not found")
    try:
        for prop_name, prop_value in properties.items():
            try:
                setattr(obj, prop_name, prop_value)
            except Exception:
                raise ValueError(
                    f"Failed to set property {prop_name} to {prop_value}")
    except Exception:
        raise
    _finish(doc)
    return f"Successfully edited {object_name} with properties {properties}"


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


def _impl_export_obj(filepath: str):
    """Export visible objects to a Wavefront OBJ file using FreeCAD's Mesh module."""
    doc = _active_doc()

    # Filter for visible objects only (skip hidden tools/base objects)
    visible_objs = [
        obj for obj in doc.Objects
        if hasattr(obj, "ViewObject") and obj.ViewObject and obj.ViewObject.Visibility
    ]

    if not visible_objs:
        return "Error: No visible objects to export."

    import Mesh
    # Mesh.export expects a list of objects and a filename
    Mesh.export(visible_objs, filepath)
    return "Exported successfully."


def _impl_pattern_linear(id: str, target_id: str, direction: dict, distance: float, count: int):
    """Create a linear pattern of a target object.
    Args:
        id: Unique ID for the pattern result object
        target_id: Name of the target object to pattern
        direction: Dict with x, y, z components of the direction vector
        distance: Distance between copies (step distance)
        count: Number of copies (including original)
    """
    # Cast inputs to safe types
    c_count = int(count)
    c_distance = float(distance)

    doc = _active_doc()
    target_obj = doc.getObject(target_id)
    if not target_obj:
        raise RuntimeError(f"Target object {target_id} not found")

    # Ensure direction components are floats
    dir_vec = App.Vector(
        float(direction.get("x", 0)),
        float(direction.get("y", 0)),
        float(direction.get("z", 0))
    )
    if dir_vec.Length == 0:
        raise ValueError("Direction vector cannot be zero")

    # Normalize to get unit direction
    unit_dir = dir_vec.normalize()

    shapes = []
    for i in range(c_count):
        shape_copy = target_obj.Shape.copy()
        # Translate by i * step in the direction
        translation = unit_dir.multiply(c_distance * i)
        shape_copy.translate(translation)
        shapes.append(shape_copy)

    # Fuse all shapes together
    if len(shapes) > 1:
        final_shape = shapes[0].multiFuse(shapes[1:])
    else:
        final_shape = shapes[0]

    pattern_obj = doc.addObject("Part::Feature", id)
    pattern_obj.Shape = final_shape

    # Hide the original object
    target_obj.ViewObject.Visibility = False
    _finish(pattern_obj)
    return f"Successfully created linear pattern '{id}' of '{target_id}' with count {c_count} in direction {direction} distance {c_distance}."


def _impl_pattern_circular(id: str, target_id: str, axis_origin: dict, axis_direction: dict, angle: float, count: int):
    """Create a circular pattern of a target object around an axis.
    Args:
        id: Unique ID for the pattern result object
        target_id: Name of the target object to pattern
        axis_origin: Dict with x, y, z for a point on the axis
        axis_direction: Dict with x, y, z for the axis direction vector
        angle: Total angle to cover in degrees (e.g., 360 for full circle)
        count: Number of copies (including original)
    """
    # Cast inputs to safe types
    c_count = int(count)
    c_angle = float(angle)

    doc = _active_doc()
    target_obj = doc.getObject(target_id)
    if not target_obj:
        raise RuntimeError(f"Target object {target_id} not found")

    # Ensure axis_origin and axis_direction components are floats
    center = App.Vector(
        float(axis_origin.get("x", 0)),
        float(axis_origin.get("y", 0)),
        float(axis_origin.get("z", 0))
    )
    axis = App.Vector(
        float(axis_direction.get("x", 0)),
        float(axis_direction.get("y", 0)),
        float(axis_direction.get("z", 0))
    )
    if axis.Length == 0:
        raise ValueError("Axis direction vector cannot be zero")

    # Normalize axis (though rotate doesn't require unit vector, we do it for consistency)
    axis = axis.normalize()

    # Angle per step in degrees
    if c_count > 1:
        step_angle = c_angle / c_count
    else:
        step_angle = 0.0

    shapes = []
    for i in range(c_count):
        shape_copy = target_obj.Shape.copy()
        # FreeCAD's native rotate method handles rotation around a point natively in degrees
        shape_copy.rotate(center, axis, step_angle * i)
        shapes.append(shape_copy)

    # Fuse all shapes together
    if len(shapes) > 1:
        final_shape = shapes[0].multiFuse(shapes[1:])
    else:
        final_shape = shapes[0]

    pattern_obj = doc.addObject("Part::Feature", id)
    pattern_obj.Shape = final_shape

    # Hide the original object
    target_obj.ViewObject.Visibility = False
    _finish(pattern_obj)
    return f"Successfully created circular pattern '{id}' of '{target_id}' with count {c_count} around axis {axis_origin}->{axis_direction} angle {c_angle}°."


# --------------------------------------------------------------------------- #
# Topology Implementations (state / faces / edges queries)
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Sketch & Extrude Implementations
# --------------------------------------------------------------------------- #


def _impl_sketch(id: str, face_ref: str, shapes: list):
    """Create a 2D sketch on a face of an existing object.

    Args:
        id: Unique ID for the sketch object
        face_ref: Opaque pointer format "ObjectName_face_N" (1-based index)
        shapes: List of 2D shape dicts with 'type' ('circle' or 'rectangle'),
                x, y (center), and radius/width/height
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

    # Get face center and normal
    center = face.CenterOfMass

    # Get normal vector (pointing outward from face)
    normal = FreeCAD.Vector(0, 0, 1)  # default fallback
    if hasattr(face.Surface, "Axis"):
        normal = face.Surface.Axis

    # Create placement to map 2D sketch plane to 3D face
    # The sketch plane XY axes need to be aligned with the face's local UV directions
    # For simplicity, we use the face normal and derive X/Y from it
    # Use a standard alignment: X = normal.cross(FreeCAD.Vector(0,0,1)) or similar
    try:
        z_axis = normal
        # Pick a reference vector not parallel to z_axis
        if abs(z_axis.z) < 0.9:
            ref = FreeCAD.Vector(0, 0, 1)
        else:
            ref = FreeCAD.Vector(1, 0, 0)
        x_axis = (z_axis.cross(ref)).normalize()
        y_axis = (z_axis.cross(x_axis)).normalize()
    except Exception:
        # Fallback
        x_axis = FreeCAD.Vector(1, 0, 0)
        y_axis = FreeCAD.Vector(0, 1, 0)
        z_axis = FreeCAD.Vector(0, 0, 1)

    placement = FreeCAD.Placement(
        center, FreeCAD.Rotation(x_axis, y_axis, z_axis))

    # Create sketch shapes in 2D (XY plane)
    shape_list = []
    for shape in shapes:
        shape_type = shape.get("type")
        x = float(shape.get("x", 0))
        y = float(shape.get("y", 0))

        if shape_type == "circle":
            radius = float(shape.get("radius"))
            # Create circle in XY plane at (x, y)
            circle = Part.makeCircle(radius, FreeCAD.Vector(
                x, y, 0), FreeCAD.Vector(0, 0, 1))
            shape_list.append(circle)
        elif shape_type == "rectangle":
            width = float(shape.get("width"))
            height = float(shape.get("height"))
            # Create rectangle centered at (x, y)
            half_w = width / 2.0
            half_h = height / 2.0
            points = [
                FreeCAD.Vector(x - half_w, y - half_h, 0),
                FreeCAD.Vector(x + half_w, y - half_h, 0),
                FreeCAD.Vector(x + half_w, y + half_h, 0),
                FreeCAD.Vector(x - half_w, y + half_h, 0),
                FreeCAD.Vector(x - half_w, y - half_h, 0),  # close the loop
            ]
            rect = Part.makePolygon(points)
            shape_list.append(rect)
        else:
            raise ValueError(f"Unknown shape type: {shape_type}")

    # Combine shapes into a compound
    if len(shape_list) == 1:
        compound = shape_list[0]
    else:
        compound = Part.Compound(shape_list)

    # Apply placement to move compound to 3D face
    compound = compound.transformGeometry(placement.toMatrix())

    # Create sketch feature
    sketch_obj = doc.addObject("Part::Feature", id)
    sketch_obj.Shape = compound

    # Store the target body name for later extrusion cuts
    sketch_obj.addProperty("App::PropertyString", "TargetBody", "PieCAD")
    sketch_obj.TargetBody = target_name

    _sync(doc)
    return f"Successfully created sketch '{id}' on face {face_ref} with {len(shapes)} shape(s)."


def _impl_extrude(id: str, sketch_id: str, depth: float, is_cut: bool = False, is_solid: bool = True):
    """Extrude a sketch to create a solid or a cut.

    Args:
        id: Unique ID for the resulting solid/cut object
        sketch_id: ID of the sketch to extrude
        depth: Extrusion depth (positive)
        is_cut: If True, perform boolean cut against TargetBody; if False, create solid
        is_solid: If True, creates a solid 3D body. If False, creates a hollow surface/shell.
    """
    import FreeCAD

    doc = _active_doc()
    sketch_obj = doc.getObject(sketch_id)
    if not sketch_obj:
        raise RuntimeError(f"Sketch {sketch_id} not found")

    # Create the extrusion object using Part::Extrusion
    extrude_name = f"{id}_tool" if is_cut else id
    extrude_obj = doc.addObject("Part::Extrusion", extrude_name)
    extrude_obj.Base = sketch_obj
    extrude_obj.DirMode = "Normal"
    extrude_obj.LengthFwd = float(depth)
    extrude_obj.Solid = is_solid
    doc.recompute()

    if is_cut:
        # Get target body from sketch property
        if not hasattr(sketch_obj, "TargetBody") or not sketch_obj.TargetBody:
            raise ValueError(
                f"Sketch '{sketch_id}' has no TargetBody property for cut operation")

        target_name = sketch_obj.TargetBody
        target = doc.getObject(target_name)
        if target is None:
            raise ValueError(f"Target body not found: {target_name}")

        # Perform the cut
        cut = doc.addObject("Part::Cut", id)
        cut.Base = target
        cut.Tool = extrude_obj

        # Hide the base and tool objects
        try:
            target.ViewObject.Visibility = False
        except Exception:
            pass
        try:
            extrude_obj.ViewObject.Visibility = False
        except Exception:
            pass

        result_obj = cut
    else:
        # For non-cut, the extrude_obj is the result
        result_obj = extrude_obj

    # Hide the sketch object
    try:
        sketch_obj.ViewObject.Visibility = False
    except Exception:
        pass

    _sync(doc)
    return f"Successfully {'cut' if is_cut else 'extruded'} '{id}' from sketch '{sketch_id}' with depth {depth} (solid={is_solid})."


def _impl_shell(id, target_id, face_refs, thickness):
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


# --------------------------------------------------------------------------- #
# IMPLEMENTATIONS registry
# --------------------------------------------------------------------------- #

_IMPLEMENTATIONS = {
    "create_box": _impl_create_box,
    "create_cylinder": _impl_create_cylinder,
    "boolean": _impl_boolean,
    "set_param": _impl_set_param,
    "get_state": _impl_get_state,
    "delete_object": _impl_delete_object,
    "translate": _impl_translate,
    "get_faces": _impl_get_faces,
    "get_edges": _impl_get_edges,
    "hole": _impl_hole,
    "edit_object": _impl_edit_object,
    "sketch": _impl_sketch,
    "extrude": _impl_extrude,
    "fillet": _impl_fillet,
    "chamfer": _impl_chamfer,
    "export_obj": _impl_export_obj,
    "clear_document": _impl_clear_document,
    "pattern_linear": _impl_pattern_linear,
    "pattern_circular": _impl_pattern_circular,
    "shell": _impl_shell,
}


# --------------------------------------------------------------------------- #
# Main-thread executor (QTimer consumer).
# --------------------------------------------------------------------------- #


def _process_queue():
    """Drain pending operations. Runs on the FreeCAD main thread via QTimer."""
    while True:
        try:
            req_id, op_name, args, kwargs = _WORK_QUEUE.get_nowait()
        except queue.Empty:
            break

        if isinstance(op_name, str):
            # String op-name form: look up the implementation in the registry.
            impl = _IMPLEMENTATIONS.get(op_name)
            if impl is None:
                status, payload = "error", f"Unknown operation: {op_name}"
            else:
                try:
                    status, payload = "ok", impl(
                        *args, **kwargs)
                except Exception as e:
                    status, payload = "error", str(e)
        else:
            # Callable form: op_name is already the implementation function.
            try:
                status, payload = "ok", op_name(
                    *args, **kwargs)
            except Exception as e:
                status, payload = "error", str(e)

        with _RESULTS_LOCK:
            _RESULTS[req_id] = (status, payload)
            event = _RESULTS_EVENTS.get(req_id)
            if event is not None:
                event.set()

        _WORK_QUEUE.task_done()


_TIMER = None


def install_main_thread_processor(interval_ms=50):
    """Install the QTimer consumer. MUST be called on the main thread."""
    global _TIMER
    if _TIMER is not None:
        return _TIMER
    _TIMER = QtCore.QTimer()
    _TIMER.timeout.connect(_process_queue)
    _TIMER.start(interval_ms)
    return _TIMER


# --------------------------------------------------------------------------- #
# Dispatcher: called on an XML-RPC worker thread, delegates to main thread.
# --------------------------------------------------------------------------- #


def _execute_on_main_thread(op_name, *args, **kwargs):
    req_id = uuid.uuid4().hex
    event = threading.Event()

    with _RESULTS_LOCK:
        _RESULTS_EVENTS[req_id] = event

    _WORK_QUEUE.put((req_id, op_name, args, kwargs))

    event.wait()

    with _RESULTS_LOCK:
        status, payload = _RESULTS.pop(req_id, ("error", "No result produced"))
        _RESULTS_EVENTS.pop(req_id, None)

    if status == "ok":
        return payload
    raise RuntimeError(payload)


# --------------------------------------------------------------------------- #
# XML-RPC handlers (thin wrappers; the real work runs on the main thread).
# --------------------------------------------------------------------------- #


def create_box(length, width, height, object_name="Box"):
    return _execute_on_main_thread("create_box", length, width, height, object_name)


def create_cylinder(radius, height, object_name="Cylinder"):
    return _execute_on_main_thread("create_cylinder", radius, height, object_name)


def boolean(operation, base_obj, tool_obj, result_name="Cut"):
    return _execute_on_main_thread("boolean", operation, base_obj, tool_obj, result_name)


def set_param(object_name, param_name, value):
    return _execute_on_main_thread("set_param", object_name, param_name, value)


def get_state():
    return _execute_on_main_thread("get_state")


def delete_object(object_name):
    return _execute_on_main_thread("delete_object", object_name)


def translate(object_name, x, y, z):
    return _execute_on_main_thread("translate", object_name, x, y, z)


def get_faces(object_name):
    return _execute_on_main_thread("get_faces", object_name)


def get_edges(object_name):
    return _execute_on_main_thread("get_edges", object_name)


def hole(id, target_id, origin, direction, diameter, depth, kind="simple", thread_spec=None):
    return _execute_on_main_thread("hole", id, target_id, origin, direction, diameter, depth, kind, thread_spec)


def edit_object(object_name, properties):
    return _execute_on_main_thread("edit_object", object_name, properties)


def export_obj(filepath):
    return _execute_on_main_thread("export_obj", filepath)


def sketch(id, face_ref, shapes):
    return _execute_on_main_thread("sketch", id, face_ref, shapes)


def extrude(id, sketch_id, depth, is_cut=False, is_solid=True):
    return _execute_on_main_thread("extrude", id, sketch_id, depth, is_cut, is_solid)


def fillet(id, target_id, edge_refs, radius):
    return _execute_on_main_thread("fillet", id, target_id, edge_refs, radius)


def chamfer(id, target_id, edge_refs, size):
    return _execute_on_main_thread("chamfer", id, target_id, edge_refs, size)


def clear_document():
    return _execute_on_main_thread("clear_document")


_HANDLERS = {
    "create_box": create_box,
    "create_cylinder": create_cylinder,
    "boolean": boolean,
    "set_param": set_param,
    "get_state": get_state,
    "delete_object": delete_object,
    "translate": translate,
    "get_faces": get_faces,
    "get_edges": get_edges,
    "hole": hole,
    "edit_object": edit_object,
    "sketch": sketch,
    "extrude": lambda *args: _execute_on_main_thread(_impl_extrude, *args),
    "fillet": fillet,
    "chamfer": chamfer,
    "export_obj": export_obj,
    "clear_document": clear_document,
    "pattern_linear": lambda *args: _execute_on_main_thread(_impl_pattern_linear, *args),
    "pattern_circular": lambda *args: _execute_on_main_thread(_impl_pattern_circular, *args),
    "shell": lambda *args: _execute_on_main_thread(_IMPLEMENTATIONS["shell"], *args),
}


# --------------------------------------------------------------------------- #
# Server startup
# --------------------------------------------------------------------------- #

_SERVER = None


def start(host="127.0.0.1", port=9876):
    global _SERVER
    if _SERVER is not None:
        App.Console.PrintWarning(
            "[PieCAD] Bridge already running; skipping duplicate start.\n"
        )
        return

    server = xmlrpc.server.SimpleXMLRPCServer(
        (host, port), allow_none=True, logRequests=False
    )
    server.register_introspection_functions()
    for name, handler in _HANDLERS.items():
        server.register_function(handler, name)

    _SERVER = server

    App.Console.PrintMessage(
        f"[PieCAD] XML-RPC bridge listening on http://{host}:{port}\n"
    )
    server.serve_forever()


def is_running() -> bool:
    return _SERVER is not None


if __name__ == "__main__":
    install_main_thread_processor()
    start()
