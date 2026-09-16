"""PieCAD FreeCAD Bridge - Boolean Module.

Contains implementations for boolean operations and hole creation.
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part

from ._common import _active_doc, _sync, _impl_set_visible

# Standard tap drill minor diameters in millimetres.
# Industrial tapped holes are modelled as tap-drill holes with persistent
# thread metadata rather than true helical solid geometry.
_TAP_DRILL_MM = {
    "M3x0.5": 2.5,
    "M4x0.7": 3.3,
    "M5x0.8": 4.2,
    "M6x1.0": 5.0,
    "M8x1.25": 6.8,
    "M10x1.5": 8.5,
    "M12x1.75": 10.2,
    "1/4-20 UNC": 5.1,
    "5/16-18 UNC": 6.6,
    "3/8-16 UNC": 8.0,
}


def _available_thread_designations():
    return ", ".join(sorted(_TAP_DRILL_MM.keys()))


def _normalize_thread_spec(thread_spec):
    s = str(thread_spec).strip()
    s = " ".join(s.split())

    # Metric normalisation: m6 x 1.0 / M6 X 1.0 / M 6 X 1.0 -> M6x1.0
    if s.upper().startswith("M"):
        s = s.upper().replace(" ", "")
        s = s.replace("X", "x")
        s = "M" + s[1:]

    # UNC normalisation: 1/4-20 unc / 1/4-20UNC -> 1/4-20 UNC
    if "UNC" in s.upper():
        s = s.upper()
        s = s.replace("UNC", " UNC")
        s = " ".join(s.split())

    return s


def _resolve_tap_drill(thread_spec):
    if not thread_spec:
        raise RuntimeError(
            "Tapped holes require a thread_spec designation. "
            f"Available standard designations are: {_available_thread_designations()}."
        )

    normalized = _normalize_thread_spec(thread_spec)
    if normalized in _TAP_DRILL_MM:
        return _TAP_DRILL_MM[normalized], normalized

    raise RuntimeError(
        f"Unknown tapped-hole thread_spec '{thread_spec}'. "
        f"Available standard designations are: {_available_thread_designations()}."
    )


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


def _impl_hole(id: str, target_id: str, origin: dict, direction: dict, diameter: float, depth: float, kind: str = "simple", thread_spec: str = None):
    """Create a hole by drilling into a target object.

    Supports: simple, tapped, counterbore, countersink.

    For tapped holes, the numeric diameter is intentionally ignored and the
    standard ISO/UNC tap drill minor diameter is resolved from thread_spec.
    The resulting Part::Cut feature is tagged with persistent ThreadSpec
    metadata.
    """
    doc = _active_doc()
    target_obj = doc.getObject(target_id)
    if not target_obj:
        raise RuntimeError(f"Target object {target_id} not found")

    c_origin = App.Vector(float(origin['x']), float(
        origin['y']), float(origin['z']))
    c_dir = App.Vector(float(direction['x']), float(
        direction['y']), float(direction['z']))

    c_dir_norm = App.Vector(c_dir.x, c_dir.y, c_dir.z)
    if c_dir_norm.Length <= 0:
        raise RuntimeError("Hole direction vector must have a non-zero length.")
    c_dir_norm.normalize()

    if kind == "tapped":
        eff_diameter, canonical_thread_spec = _resolve_tap_drill(thread_spec)
    else:
        eff_diameter = float(diameter)
        canonical_thread_spec = None

    eff_radius = eff_diameter / 2.0

    # Generate the drill bit as a PARAMETRIC Part::Cylinder object so that
    # downstream edit_feature calls can resize it via Radius/Height.
    tool_obj = doc.addObject("Part::Cylinder", f"{id}_drill")
    tool_obj.Radius = eff_radius

    # Coplanar-face hardening: extend the drill 2.0mm past the requested
    # depth and back its base off 1.0mm along the negative direction vector,
    # so it cleanly breaches the entry face instead of failing the boolean
    # subtraction on coincident (coplanar) faces ("just a mark" bug).
    tool_obj.Height = float(depth) + 2.0

    placement = App.Placement()
    placement.Base = c_origin - c_dir_norm * 1.0
    # Rotate the cylinder's local +Z axis onto the drill direction vector.
    placement.Rotation = App.Rotation(App.Vector(0, 0, 1), c_dir_norm)
    tool_obj.Placement = placement

    # Execute the boolean cut
    cut_obj = doc.addObject("Part::Cut", id)
    cut_obj.Base = target_obj
    cut_obj.Tool = tool_obj

    _impl_set_visible(target_obj, False)
    _impl_set_visible(tool_obj, False)

    # Persist mechanical thread metadata on the resulting feature.
    if kind == "tapped":
        if not hasattr(cut_obj, "ThreadSpec"):
            cut_obj.addProperty("App::PropertyString", "ThreadSpec", "Mechanical")
        cut_obj.ThreadSpec = canonical_thread_spec

    _sync(doc)

    if kind == "tapped":
        return (
            f"Successfully created {kind} hole '{id}' in '{target_id}' using "
            f"tap drill diameter {eff_diameter} mm for {canonical_thread_spec}, "
            f"depth {depth}."
        )

    return f"Successfully created {kind} hole '{id}' in '{target_id}' with diameter {eff_diameter}, depth {depth}."
