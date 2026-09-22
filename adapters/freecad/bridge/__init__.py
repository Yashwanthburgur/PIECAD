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
import io
import traceback
import contextlib
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
    _impl_get_mass_properties,
    _impl_get_bom,
    _impl_interference_check,
)

# Import primitive and boolean implementations
from .primitives import _impl_create_box, _impl_create_cylinder
from .boolean import _impl_boolean, _impl_hole
from .features import _impl_fillet, _impl_chamfer, _impl_shell, _impl_edit_feature
from .sketch import _impl_sketch, _impl_extrude
from .patterns import _impl_pattern_linear, _impl_pattern_circular
from .assembly import _impl_mate
from .export import _impl_export_model, export_current_state as _impl_export_current_state

# Dynamically resolve project root (two levels up from this file's directory)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# --------------------------------------------------------------------------- #
# Ping & Execute (for MCP server compatibility)
# --------------------------------------------------------------------------- #


def ping():
    """Health check for MCP server. Returns True if bridge is responsive."""
    return True


def execute(code: str):
    """Execute Python code in a sandboxed environment for MCP server compatibility.

    This provides a restricted execution environment that allows FreeCAD geometry
    operations while blocking dangerous builtins and OS-level access.

    Args:
        code: Python code string to execute. Should assign result to `_result_` variable.

    Returns:
        Dict with keys: success (bool), result (any), stdout (str), stderr (str)
    """
    # Restricted builtins - disable dangerous functions
    safe_builtins = {
        # Basic types and safe functions
        "bool": bool,
        "int": int,
        "float": float,
        "str": str,
        "list": list,
        "tuple": tuple,
        "dict": dict,
        "set": set,
        "frozenset": frozenset,
        "len": len,
        "range": range,
        "enumerate": enumerate,
        "zip": zip,
        "map": map,
        "filter": filter,
        "sum": sum,
        "min": min,
        "max": max,
        "abs": abs,
        "round": round,
        "pow": pow,
        "divmod": divmod,
        "all": all,
        "any": any,
        "isinstance": isinstance,
        "issubclass": issubclass,
        "hasattr": hasattr,
        "getattr": getattr,
        "setattr": setattr,
        "delattr": delattr,
        "type": type,
        "object": object,
        "slice": slice,
        "property": property,
        "staticmethod": staticmethod,
        "classmethod": classmethod,
        "print": print,
        "repr": repr,
        "format": format,
        "ord": ord,
        "chr": chr,
        "hex": hex,
        "oct": oct,
        "bin": bin,
        "id": id,
        "hash": hash,
        "iter": iter,
        "next": next,
        "reversed": reversed,
        "sorted": sorted,
        "vars": vars,
        "dir": dir,
        "callable": callable,
        "Exception": Exception,
        "BaseException": BaseException,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "RuntimeError": RuntimeError,
        "AttributeError": AttributeError,
        "KeyError": KeyError,
        "IndexError": IndexError,
        "StopIteration": StopIteration,
        "NotImplementedError": NotImplementedError,
        "NameError": NameError,
        "ImportError": ImportError,
        "KeyboardInterrupt": KeyboardInterrupt,
        "SystemExit": SystemExit,
        "ZeroDivisionError": ZeroDivisionError,
        "ArithmeticError": ArithmeticError,
        "AssertionError": AssertionError,
        "BufferError": BufferError,
        "EOFError": EOFError,
        "GeneratorExit": GeneratorExit,
        "MemoryError": MemoryError,
        "OSError": OSError,
        "OverflowError": OverflowError,
        "RecursionError": RecursionError,
        "ReferenceError": ReferenceError,
        "SyntaxError": SyntaxError,
        "SystemError": SystemError,
        "UnicodeError": UnicodeError,
        "Warning": Warning,
    }

    # Construct restricted globals
    restricted_globals = {
        "__builtins__": safe_builtins,
        # FreeCAD modules
        "FreeCAD": App,
        "App": App,
        "Part": Part,
        "Draft": __import__("Draft") if "Draft" not in globals() else globals()["Draft"],
        "math": __import__("math"),
    }

    # Capture stdout/stderr
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()

    try:
        with contextlib.redirect_stdout(stdout_capture):
            with contextlib.redirect_stderr(stderr_capture):
                # Execute the code
                local_vars = {}
                exec(code, restricted_globals, local_vars)

        # Extract _result_ from locals
        result = local_vars.get("_result_", None)

        return {
            "success": True,
            "result": result,
            "stdout": stdout_capture.getvalue(),
            "stderr": stderr_capture.getvalue(),
        }

    except Exception as e:
        # Capture full traceback
        tb_str = traceback.format_exc()
        stderr_capture.write(tb_str)

        return {
            "success": False,
            "result": None,
            "stdout": stdout_capture.getvalue(),
            "stderr": stderr_capture.getvalue(),
        }


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
# Document-level helpers (runs ONLY on the main thread).
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
    doc.recompute()
    try:
        Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
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
    doc.recompute()
    try:
        Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
    return f"Successfully edited {object_name} with properties {properties}"


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
    "edit_feature": _impl_edit_feature,
    "sketch": _impl_sketch,
    "extrude": _impl_extrude,
    "fillet": _impl_fillet,
    "chamfer": _impl_chamfer,
    "export_obj": _impl_export_obj,
    "clear_document": _impl_clear_document,
    "pattern_linear": _impl_pattern_linear,
    "pattern_circular": _impl_pattern_circular,
    "shell": _impl_shell,
    "mate": _impl_mate,
    "interference_check": _impl_interference_check,
    "get_mass_properties": _impl_get_mass_properties,
    "get_bom": _impl_get_bom,
    "export_model": _impl_export_model,
    "export_current_state": _impl_export_current_state,
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


def fillet(id, target_id, edge_refs, radius, topology_version=None):
    return _execute_on_main_thread("fillet", id, target_id, edge_refs, radius, topology_version)


def chamfer(id, target_id, edge_refs, size, topology_version=None):
    return _execute_on_main_thread("chamfer", id, target_id, edge_refs, size, topology_version)


def clear_document():
    return _execute_on_main_thread("clear_document")


def pattern_linear(id, target_id, direction, distance, count):
    return _execute_on_main_thread("pattern_linear", id, target_id, direction, distance, count)


def pattern_circular(id, target_id, axis_origin, axis_direction, angle, count):
    return _execute_on_main_thread("pattern_circular", id, target_id, axis_origin, axis_direction, angle, count)


def shell(id, target_id, face_refs, thickness):
    return _execute_on_main_thread("shell", id, target_id, face_refs, thickness)


def mate(id, mate_type, moving_target, moving_ref, fixed_target, fixed_ref, offset, flip):
    return _execute_on_main_thread("mate", id, mate_type, moving_target, moving_ref, fixed_target, fixed_ref, offset, flip)


def interference_check(id, part_ids=None):
    return _execute_on_main_thread("interference_check", id, part_ids)


def get_mass_properties(id, object_name):
    return _execute_on_main_thread("get_mass_properties", id, object_name)


def get_bom(id):
    return _execute_on_main_thread("get_bom", id)


def export_model(id, format_type, filepath):
    return _execute_on_main_thread("export_model", id, format_type, filepath)


def export_current_state(filepath, format="glb"):
    return _execute_on_main_thread("export_current_state", filepath, format)


def edit_feature(id, target_id, parameters):
    return _execute_on_main_thread("edit_feature", id, target_id, parameters)


_HANDLERS = {
    # MCP server compatibility
    "ping": ping,
    "execute": execute,
    # Geometry operations
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
    "edit_feature": edit_feature,
    "sketch": sketch,
    "extrude": extrude,
    "fillet": fillet,
    "chamfer": chamfer,
    "export_obj": export_obj,
    "clear_document": clear_document,
    "pattern_linear": pattern_linear,
    "pattern_circular": pattern_circular,
    "shell": shell,
    "mate": mate,
    "interference_check": interference_check,
    "get_mass_properties": get_mass_properties,
    "get_bom": get_bom,
    "export_model": export_model,
    "export_current_state": export_current_state,
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
