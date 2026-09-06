from typing import Literal, Union, Annotated, Optional
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
    face_ref: FaceRef
    x: float
    y: float
    diameter: float = Field(gt=0)
    depth: Optional[float] = Field(
        default=None, description="None = through-all")
    kind: Literal["simple", "counterbore", "countersink", "tapped"] = "simple"
    thread_spec: Optional[str] = Field(default=None, description="e.g. 'M6x1'")


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
