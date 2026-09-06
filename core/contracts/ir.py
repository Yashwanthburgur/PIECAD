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


# --- PieCAD Remote (mobile viewer) ---
class RemoteIntent(BaseModel):
    """Structured intent captured by PieCAD Remote (mobile viewer).

    The viewer only ever supplies WHERE (which object, and the exact 3D
    point that was touched, in FreeCAD's mm/Z-up convention) plus the
    user's raw utterance. Resolving that point to a specific face, and
    deciding WHAT CAD operation it means, happens entirely on the backend/
    agent side -- the viewer stays CAD-agnostic, same principle as the
    FreeCAD panel from Sprint 3A.
    """
    session_id: str
    cad_object_name: str
    world_position: Vec3
    text: str
