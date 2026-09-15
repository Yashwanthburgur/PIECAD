"""PieCAD FreeCAD Bridge - Assembly Module.

Contains implementations for multi-part assembly constraints (mates), ported
from the former bridge monolith: pure B-Rep transformation matrices for
coincident and concentric mates.
"""

import FreeCAD as App
import FreeCADGui as Gui


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


def _get_subelement(obj, ref_str):
    """Extracts a Face or Edge from an object given a reference string."""
    ref_clean = str(ref_str).strip()
    idx = None
    is_edge = "edge" in ref_clean.lower()

    if "_" in ref_clean:
        parts = ref_clean.split("_")
        if parts[-1].isdigit():
            idx = int(parts[-1]) - 1
    elif ref_clean.lower().startswith("face") or ref_clean.lower().startswith("edge"):
        digits = "".join(filter(str.isdigit, ref_clean))
        if digits:
            idx = int(digits) - 1
    elif ref_clean.isdigit():
        idx = int(ref_clean) - 1

    shape = obj.Shape
    if is_edge:
        if idx is not None and 0 <= idx < len(shape.Edges):
            return shape.Edges[idx], "edge"
    else:
        if idx is not None and 0 <= idx < len(shape.Faces):
            return shape.Faces[idx], "face"

    if hasattr(shape, "Faces") and len(shape.Faces) > 0:
        return shape.Faces[0], "face"
    raise RuntimeError(
        f"Could not resolve subelement '{ref_str}' on {obj.Name}")


def _impl_mate(id, mate_type, moving_target, moving_ref, fixed_target, fixed_ref, offset, flip):
    doc = App.ActiveDocument
    moving_obj = doc.getObject(moving_target)
    fixed_obj = doc.getObject(fixed_target)

    if not moving_obj:
        raise RuntimeError(f"Moving target '{moving_target}' not found")
    if not fixed_obj:
        raise RuntimeError(f"Fixed target '{fixed_target}' not found")

    m_elem, m_kind = _get_subelement(moving_obj, moving_ref)
    f_elem, f_kind = _get_subelement(fixed_obj, fixed_ref)

    mate_type_clean = str(mate_type).strip().lower()

    if mate_type_clean == "concentric":
        def get_axis_and_center(elem):
            if hasattr(elem, "Surface") and hasattr(elem.Surface, "Axis"):
                return elem.Surface.Axis, elem.CenterOfMass
            if hasattr(elem, "Curve") and hasattr(elem.Curve, "Axis"):
                return elem.Curve.Axis, elem.CenterOfMass
            if hasattr(elem, "normalAt"):
                return elem.normalAt(0, 0), elem.CenterOfMass
            return App.Vector(0, 0, 1), elem.CenterOfMass

        m_axis, m_center = get_axis_and_center(m_elem)
        f_axis, f_center = get_axis_and_center(f_elem)

        target_axis = -f_axis if flip else f_axis

        if m_axis.cross(target_axis).Length > 1e-5 or m_axis.dot(target_axis) < 0.9999:
            rot = App.Rotation(m_axis, target_axis)
            moving_obj.Placement.Rotation = rot.multiply(
                moving_obj.Placement.Rotation)
            doc.recompute()
            m_elem, _ = _get_subelement(moving_obj, moving_ref)
            _, m_center = get_axis_and_center(m_elem)

        delta = f_center - m_center
        axis_component = target_axis.multiply(delta.dot(target_axis))
        perp_shift = delta - axis_component

        if abs(offset) > 1e-5:
            perp_shift += target_axis.multiply(float(offset))

        moving_obj.Placement.Base += perp_shift

    elif mate_type_clean == "coincident":
        def get_normal_and_center(elem):
            if hasattr(elem, "normalAt"):
                u_mid = (elem.ParameterRange[0] + elem.ParameterRange[1]) / 2.0
                v_mid = (elem.ParameterRange[2] + elem.ParameterRange[3]) / 2.0
                return elem.normalAt(u_mid, v_mid), elem.CenterOfMass
            return App.Vector(0, 0, 1), elem.CenterOfMass

        m_norm, m_center = get_normal_and_center(m_elem)
        f_norm, f_center = get_normal_and_center(f_elem)

        target_norm = f_norm if flip else -f_norm

        if m_norm.cross(target_norm).Length > 1e-5 or m_norm.dot(target_norm) < 0.9999:
            rot = App.Rotation(m_norm, target_norm)
            moving_obj.Placement.Rotation = rot.multiply(
                moving_obj.Placement.Rotation)
            doc.recompute()
            m_elem, _ = _get_subelement(moving_obj, moving_ref)
            _, m_center = get_normal_and_center(m_elem)

        plane_distance = (f_center - m_center).dot(f_norm)
        normal_shift = f_norm.multiply(plane_distance + float(offset))
        moving_obj.Placement.Base += normal_shift

    else:
        raise RuntimeError(
            f"Unsupported mate type '{mate_type}'. Use 'concentric' or 'coincident'.")

    doc.recompute()
    return _sync(doc)
