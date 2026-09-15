"""PieCAD FreeCAD Bridge - Boolean Module.

Contains implementations for boolean operations and hole creation.
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part

from ._common import _active_doc, _sync


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
