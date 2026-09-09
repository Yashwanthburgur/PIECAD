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

# Import topology functions
from .topology import (
    _impl_get_state,
    _impl_get_faces,
    _impl_get_edges,
)

# Import primitive and boolean implementations
from .primitives import _impl_create_box, _impl_create_cylinder
from .boolean import _impl_boolean, _impl_hole
from .features import _impl_fillet, _impl_chamfer
from .sketch import _impl_sketch, _impl_extrude

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


def _impl_extrude(id: str, sketch_id: str, depth: float, is_cut: bool = False):
    """Extrude a sketch to create a solid or a cut.

    Args:
        id: Unique ID for the resulting solid/cut object
        sketch_id: ID of the sketch to extrude
        depth: Extrusion depth (positive)
        is_cut: If True, perform boolean cut against TargetBody; if False, create solid
    """
    import FreeCAD

    doc = _active_doc()
    sketch_obj = doc.getObject(sketch_id)
    if sketch_obj is None:
        raise ValueError(f"Sketch object not found: {sketch_id}")

    if not hasattr(sketch_obj, "Shape") or sketch_obj.Shape is None:
        raise ValueError(f"Sketch has no Shape: {sketch_id}")

    # Get normal vector from sketch placement
    normal = sketch_obj.Placement.Rotation.Axis
    if normal is None:
        normal = FreeCAD.Vector(0, 0, 1)

    # For cuts, reverse normal to go into the material
    if is_cut:
        normal = normal * -1.0

    # Extrude vector
    extrude_vec = normal * float(depth)

    # Perform extrusion
    extruded_shape = sketch_obj.Shape.extrude(extrude_vec)

    if is_cut:
        # Get target body from sketch property
        if not hasattr(sketch_obj, "TargetBody") or not sketch_obj.TargetBody:
            raise ValueError(
                f"Sketch '{sketch_id}' has no TargetBody property for cut operation")

        target_name = sketch_obj.TargetBody
        target = doc.getObject(target_name)
        if target is None:
            raise ValueError(f"Target body not found: {target_name}")

        # Create tool object from extruded shape
        tool = doc.addObject("Part::Feature", f"{id}_tool")
        tool.Shape = extruded_shape

        # Perform the cut
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

        result_obj = cut
    else:
        # Create solid feature
        solid = doc.addObject("Part::Feature", id)
        solid.Shape = extruded_shape
        result_obj = solid

    _sync(doc)
    return f"Successfully {'cut' if is_cut else 'extruded'} '{id}' from sketch '{sketch_id}' with depth {depth}."


# Import topology functions


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
}


# --------------------------------------------------------------------------- #
# Main-thread executor (QTimer consumer).
# --------------------------------------------------------------------------- #


_WORK_QUEUE: "queue.Queue[tuple]" = queue.Queue()
_RESULTS: "dict[str, tuple[str, str]]" = {}
_RESULTS_EVENTS: "dict[str, threading.Event]" = {}
_RESULTS_LOCK = threading.Lock()


def _process_queue():
    """Drain pending operations. Runs on the FreeCAD main thread via QTimer."""
    while True:
        try:
            req_id, op_name, args, kwargs = _WORK_QUEUE.get_nowait()
        except queue.Empty:
            break

        if op_name not in _IMPLEMENTATIONS:
            status, payload = "error", f"Unknown operation: {op_name}"
        else:
            try:
                status, payload = "ok", _IMPLEMENTATIONS[op_name](
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


def hole(id, face_ref, x, y, diameter, depth=100.0):
    return _execute_on_main_thread("hole", id, face_ref, x, y, diameter, depth)


def edit_object(object_name, properties):
    return _execute_on_main_thread("edit_object", object_name, properties)


def export_obj(filepath):
    return _execute_on_main_thread("export_obj", filepath)


def sketch(id, face_ref, shapes):
    return _execute_on_main_thread("sketch", id, face_ref, shapes)


def extrude(id, sketch_id, depth, is_cut=False):
    return _execute_on_main_thread("extrude", id, sketch_id, depth, is_cut)


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
    "extrude": extrude,
    "fillet": fillet,
    "chamfer": chamfer,
    "export_obj": export_obj,
    "clear_document": clear_document,
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
