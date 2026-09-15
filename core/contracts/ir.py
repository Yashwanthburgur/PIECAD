from typing import Literal, Union, Annotated, Optional, List
from pydantic import BaseModel, Field


class Vec3(BaseModel):
    x: float
    y: float
    z: float


class OpBase(BaseModel):
    op: str
    id: str  # stable feature id, e.g. "obj_7f3a"


FaceRef = str
EdgeRef = str

# --- 2D Semantic Shapes (for Sketching) ---


class Circle2D(BaseModel):
    type: Literal["circle"] = "circle"
    x: float = Field(description="Center X relative to sketch plane")
    y: float = Field(description="Center Y relative to sketch plane")
    radius: float = Field(gt=0)


class Rectangle2D(BaseModel):
    type: Literal["rectangle"] = "rectangle"
    x: float = Field(description="Center X relative to sketch plane")
    y: float = Field(description="Center Y relative to sketch plane")
    width: float = Field(gt=0)
    height: float = Field(gt=0)


SemanticShape = Union[Circle2D, Rectangle2D]

# --- Tier 1 (Core Solid Modeling) ---


class Boolean(OpBase):
    op: Literal["boolean"] = "boolean"
    target_id: str
    tool_id: str
    mode: Literal["union", "subtract", "intersect"]


class DeleteFeature(OpBase):
    op: Literal["delete_feature"] = "delete_feature"
    target_feature_id: str


# --- Tier 1 (Core Solid Modeling) ---
class Hole(OpBase):
    op: Literal["hole"] = "hole"
    target_id: str
    origin: dict
    direction: dict
    diameter: float = Field(gt=0)
    depth: float = Field(gt=0)
    kind: Literal["simple", "tapped", "counterbore", "countersink"] = "simple"
    thread_spec: Optional[str] = Field(default=None, description="e.g. 'M6'")


# --- Sketch & Extrude (B-rep workflow) ---
class Sketch(OpBase):
    op: Literal["sketch"] = "sketch"
    face_ref: FaceRef = Field(
        description="The Opaque Pointer ID of the face to sketch on")
    shapes: list[SemanticShape] = Field(
        description="List of 2D shapes to draw on this sketch plane")


class Extrude(OpBase):
    op: Literal["extrude"] = "extrude"
    sketch_id: str = Field(description="The ID of the sketch to extrude")
    depth: float = Field(gt=0)
    is_cut: bool = Field(
        default=False, description="True = Boolean subtract (cut), False = Boolean add (pad)")
    is_solid: bool = Field(
        default=True, description="If True, creates a solid 3D body. If False, creates a hollow surface/shell.")


# --- Edge Dressing (Fillet/Chamfer) ---
class Fillet(OpBase):
    op: Literal["fillet"] = "fillet"
    target_id: str = Field(..., description="The ID of the body to fillet.")
    edge_refs: list[str] = Field(
        ..., description="List of opaque edge IDs to round (e.g. ['Box_edge_1']).")
    radius: float = Field(gt=0, description="Radius of the fillet in mm.")


class Chamfer(OpBase):
    op: Literal["chamfer"] = "chamfer"
    target_id: str = Field(..., description="The ID of the body to chamfer.")
    edge_refs: list[str] = Field(
        ..., description="List of opaque edge IDs to chamfer (e.g. ['Box_edge_1']).")
    size: float = Field(gt=0, description="Distance of the chamfer in mm.")


# --- Tier 2 (Primitives with Built-in Translation) ---
class Box(OpBase):
    op: Literal["box"] = "box"
    length: float = Field(gt=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    origin: Vec3 = Vec3(x=0, y=0, z=0)


class Cylinder(OpBase):
    op: Literal["cylinder"] = "cylinder"
    radius: float = Field(gt=0)
    height: float = Field(gt=0)
    origin: Vec3 = Vec3(x=0, y=0, z=0)


class LinearPattern(OpBase):
    op: Literal["pattern_linear"] = "pattern_linear"
    target_id: str
    direction: dict
    distance: float
    count: int


class CircularPattern(OpBase):
    op: Literal["pattern_circular"] = "pattern_circular"
    target_id: str
    axis_origin: dict
    axis_direction: dict
    angle: float  # in degrees, e.g., 360.0
    count: int


class Shell(OpBase):
    """Hollows out a solid 3D body into an open thin-walled container or enclosure."""
    id: str = Field(
        description="Unique identifier for the shell operation, e.g., 'shell_container'")
    op: Literal["shell"] = Field(
        default="shell", description="Operation type, must be 'shell'")
    target_id: str = Field(
        description="The ID of the solid object to hollow out, e.g., 'box1'")
    face_refs: List[str] = Field(
        description="List of face IDs to remove/leave open, e.g., ['box1_face_6'] for an open top")
    thickness: float = Field(
        description="Wall thickness in mm. Use negative numbers like -2.0 to hollow inward")


class Mate(BaseModel):
    """Aligns and positions two independent bodies using geometric mates."""
    id: str = Field(
        description="Unique identifier for the mate operation, e.g. 'mate_1'")
    op: Literal["mate"] = Field(
        default="mate", description="Operation type, must be 'mate'")
    mate_type: Literal["concentric", "coincident"] = Field(
        description="'concentric' aligns axes of cylinders/holes; 'coincident' brings two planar faces into flush contact"
    )
    moving_target: str = Field(
        description="Object ID of the part to be moved/transformed")
    moving_ref: str = Field(
        description="Face or Edge ID on moving_target, e.g. 'pin_face_1'")
    fixed_target: str = Field(
        description="Object ID of the reference part that stays fixed in space")
    fixed_ref: str = Field(
        description="Face or Edge ID on fixed_target, e.g. 'base_face_6'")
    offset: float = Field(
        default=0.0, description="Offset distance along the mate vector in mm")
    flip: bool = Field(
        default=False, description="Flip the alignment direction or face normals")


class GetMassProperties(BaseModel):
    """Calculates volume, center of mass, and bounding box for a solid body."""
    id: str = Field(description="Unique ID for this query")
    op: Literal["get_mass_properties"] = Field(default="get_mass_properties")
    object_name: str = Field(description="The ID of the object to analyze")


class GetBOM(BaseModel):
    """Generates a Bill of Materials (list of all independent, visible solid parts in the assembly)."""
    id: str = Field(description="Unique ID for this query")
    op: Literal["get_bom"] = Field(default="get_bom")


class ExportModel(OpBase):
    """Exports the current visible assembly to a STEP or STL file."""
    id: str = Field(description="Unique ID for this export operation")
    op: Literal["export"] = Field(
        default="export", description="Operation type, must be 'export'")
    format: Literal["step", "stl"] = Field(
        description="Export format: 'step' or 'stl'")
    filename: str = Field(
        description="Base filename without extension (e.g., 'my_part')")


# Union of all operations for use in the agent
Operation = Union[
    Boolean,
    DeleteFeature,
    Hole,
    Sketch,
    Extrude,
    Fillet,
    Chamfer,
    Box,
    Cylinder,
    LinearPattern,
    CircularPattern,
    Shell,
    Mate,
    GetMassProperties,
    GetBOM,
    ExportModel,
]
