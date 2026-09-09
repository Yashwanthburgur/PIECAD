"""PieCAD FreeCAD Bridge - Sketch Module.

Contains implementations for sketch creation and extrusion.
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part


def _active_doc():
    """Get or create the active FreeCAD document."""
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
    """Finish document operation: recompute and fit view."""
    import FreeCADGui as Gui

    doc.recompute()
    try:
        Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
    return doc


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


def _impl_set_visible(doc, name, visible):
    """Set visibility of an object."""
    import FreeCADGui as Gui

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
