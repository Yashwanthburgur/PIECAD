// The bridge exports GLB via trimesh (see bridge.py's _impl_export_glb)
// WITHOUT converting to glTF's "meters" convention -- geometry stays in
// FreeCAD's native millimeters, matching what the viewer's camera and
// controls are set up for. So no scale (or axis) conversion is needed
// here; a raw Three.js raycast point already lines up with FreeCAD's
// Shape.CenterOfMass values as-is.
//
// If you later change the exporter to scale to meters (e.g. to be
// spec-compliant for use with other glTF tools), update this constant
// AND the camera position in Viewer.jsx to match the new scale.

const GLTF_TO_FREECAD_SCALE = 1;

export function toFreeCADPoint(threePoint) {
  return {
    x: threePoint.x * GLTF_TO_FREECAD_SCALE,
    y: threePoint.y * GLTF_TO_FREECAD_SCALE,
    z: threePoint.z * GLTF_TO_FREECAD_SCALE,
  };
}