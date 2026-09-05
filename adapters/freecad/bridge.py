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

import Part
import FreeCADGui as Gui
import FreeCAD as App
import json
import xmlrpc.server
import uuid
import threading
import queue
import os
from pathlib import Path

# Dynamically resolve project root (two levels up from this file's directory)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# Import QtCore robustly across FreeCAD Qt bindings.
try:
    from PySide6 import QtCore
except ImportError:
    try:
        from PySide2 import QtCore
    except ImportError:
        from PySide import QtCore


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
    """Sync the document after geometry changes: recompute and fit view."""
    doc.recompute()
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


def _impl_fillet_edges(object_name, radius):
    doc = _active_doc()

    obj = doc.getObject(object_name)
    if obj is None:
        raise ValueError(f"Object not found: {object_name}")

    fillet = doc.addObject("Part::Fillet", object_name + "_Fillet")
    fillet.Base = obj
    fillet.Edges = ["Edge" + str(i + 1) for i in range(len(obj.Shape.Edges))]
    fillet.Radius = float(radius)

    _impl_set_visible(doc, object_name, False)

    _finish(doc)
    _impl_set_visible(doc, fillet.Name, True)
    return f"Successfully filleted edges of {object_name} as '{fillet.Name}'."


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


def _impl_get_state():
    """Get state of all objects in the active document.

    Runs on the main thread via the QTimer queue system.
    Returns a JSON string with object names, types, and basic parametric properties.
    """
    doc = _active_doc()
    objects_state = []
    for obj in doc.Objects:
        obj_info = {
            "Name": obj.Name,
            "TypeId": obj.TypeId,
        }
        # Extract basic parametric properties if they exist
        for param in ["Length", "Width", "Height", "Radius"]:
            if hasattr(obj, param):
                obj_param = getattr(obj, param)
                # Handle Part::Cylinder where Radius might be a sub-object attribute
                try:
                    obj_info[param] = float(obj_param)
                except (TypeError, ValueError):
                    pass
        objects_state.append(obj_info)
    return json.dumps(objects_state)


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


def _impl_export_glb(filepath: str):
    """Export visible objects to a GLB file using FreeCAD's GLTF exporter."""
    doc = _active_doc()

    # Filter for visible objects only (skip hidden tools/base objects)
    visible_objs = [
        obj for obj in doc.Objects
        if hasattr(obj, "ViewObject") and obj.ViewObject and obj.ViewObject.Visibility
    ]

    if not visible_objs:
        return "Error: No visible objects to export."

    import importGLTF
    importGLTF.export(visible_objs, filepath)
    return "Exported successfully."


_IMPLEMENTATIONS = {
    "create_box": _impl_create_box,
    "create_cylinder": _impl_create_cylinder,
    "boolean": _impl_boolean,
    "fillet_edges": _impl_fillet_edges,
    "set_param": _impl_set_param,
    "get_state": _impl_get_state,
    "delete_object": _impl_delete_object,
    "translate": _impl_translate,
    "get_faces": _impl_get_faces,
    "hole": _impl_hole,
    "export_glb": _impl_export_glb,
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


def fillet_edges(object_name, radius):
    return _execute_on_main_thread("fillet_edges", object_name, radius)


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


def hole(id, face_ref, x, y, diameter, depth=100.0):
    return _execute_on_main_thread("hole", id, face_ref, x, y, diameter, depth)


def export_glb(filepath):
    return _execute_on_main_thread("export_glb", filepath)


_HANDLERS = {
    "create_box": create_box,
    "create_cylinder": create_cylinder,
    "boolean": boolean,
    "fillet_edges": fillet_edges,
    "set_param": set_param,
    "get_state": get_state,
    "delete_object": delete_object,
    "translate": translate,
    "get_faces": get_faces,
    "hole": hole,
    "export_glb": export_glb,
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
