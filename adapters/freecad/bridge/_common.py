"""Common helper functions for the FreeCAD Bridge package.

DRY (Don't Repeat Yourself) - consolidated from topology.py, boolean.py,
features.py, primitives.py, sketch.py, assembly.py, patterns.py, export.py.
"""

import contextlib
import json as _json
import hashlib
import FreeCAD as App
import FreeCADGui as Gui


def _active_doc():
    """Get or create the active FreeCAD document."""
    doc = App.ActiveDocument
    if not doc:
        raise RuntimeError("No active FreeCAD document.")
    return doc


def _sync(doc=None):
    """Sync the document after geometry changes: recompute and fit view.
    Raises RuntimeError if recompute fails or if any object becomes invalid.
    """
    if doc is None:
        doc = App.ActiveDocument
    if not doc:
        return {"status": "success"}

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

    return {"status": "success"}


def _finish(obj, doc=None):
    if doc is None:
        doc = App.ActiveDocument
    if doc:
        doc.recompute()
    return {"status": "success", "id": obj.Name}


def _impl_set_visible(obj, visible=True):
    if hasattr(obj, "ViewObject") and obj.ViewObject:
        obj.ViewObject.Visibility = visible


# --------------------------------------------------------------------------- #
# Operation-identity idempotency (retry-safety)                                #
# --------------------------------------------------------------------------- #
# A retry after a lost/timed-out response must converge to the SAME logical
# operation, not silently overwrite an object that merely shares the requested
# id. We therefore stamp each produced feature with an "OpFingerprint" derived
# from (tool name, semantically relevant requested arguments, target/source
# identity). Replay semantics:
#   existing object + same fingerprint  -> already done; converge (idempotent OK)
#   existing object + different/missing fingerprint
#                                       -> genuinely different operation with
#                                          the same id; REJECT (no silent
#                                          overwrite of unrelated geometry).


def _op_fingerprint(tool: str, semantic_args: dict) -> str:
    """Deterministic fingerprint for the logical content of an operation."""
    data = _json.dumps(
        {"tool": tool, "args": semantic_args}, sort_keys=True, default=str)
    return hashlib.sha1(data.encode()).hexdigest()[:16]


def _op_stamp(obj, fingerprint: str) -> None:
    """Persist the operation fingerprint on the produced object."""
    try:
        if not hasattr(obj, "OpFingerprint"):
            obj.addProperty("App::PropertyString", "OpFingerprint", "PieCAD")
        obj.OpFingerprint = fingerprint
    except Exception:
        pass


def _op_existing_matches(doc, object_id: str, fingerprint: str):
    """Return (exists, fingerprint_matches, existing_obj)."""
    existing = doc.getObject(object_id)
    if existing is None:
        return False, False, None
    return True, getattr(existing, "OpFingerprint", None) == fingerprint, existing


@contextlib.contextmanager
def _transaction(doc, name: str):
    """Best-effort FreeCAD document transaction for an atomic mutation.

    Covers the full create/modify/hide/recompute lifecycle. On any exception
    inside the block the transaction is aborted (rolled back) where FreeCAD
    supports it; if abort itself fails the original exception still propagates
    so the caller never sees a false success.
    """
    opened = False
    try:
        doc.openTransaction(name)
        opened = True
    except Exception:
        opened = False
    try:
        yield
    except Exception:
        if opened:
            try:
                doc.abortTransaction()
            except Exception:
                pass
        raise
    else:
        if opened:
            try:
                doc.commitTransaction()
            except Exception:
                pass
