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
