import { useEffect } from "react";
import { useGLTF } from "@react-three/drei";
import * as THREE from "three";
import { computeBoundsTree, disposeBoundsTree, acceleratedRaycast } from "three-mesh-bvh";
import { toFreeCADPoint } from "../lib/coords.js";

// three-mesh-bvh: without this, raycasting against a CAD-derived mesh
// (thousands of triangles from a boolean cut, say) does a linear scan
// over every triangle on every touch. This patches in a BVH so it stays
// fast -- exactly the library the system design called out, no custom
// spatial indexing written here.
THREE.BufferGeometry.prototype.computeBoundsTree = computeBoundsTree;
THREE.BufferGeometry.prototype.disposeBoundsTree = disposeBoundsTree;
THREE.Mesh.prototype.raycast = acceleratedRaycast;

export default function SceneModel({ url, onSelect }) {
  const { scene } = useGLTF(url);

  useEffect(() => {
    scene.traverse((child) => {
      if (child.isMesh) child.geometry.computeBoundsTree();
    });
    return () => {
      scene.traverse((child) => {
        if (child.isMesh) child.geometry.disposeBoundsTree?.();
      });
    };
  }, [scene]);

  function handlePointerDown(event) {
    event.stopPropagation();
    if (!event.object) return;

    // FreeCAD's glTF exporter names each mesh node after the FreeCAD
    // object it came from (e.g. "Box001"). That IS the CAD reference --
    // no per-triangle metadata needs to be baked into the GLB for this to
    // work. The viewer only ever reports WHERE (object name + point);
    // the backend decides WHAT that means.
    onSelect({
      cad_object_name: event.object.name,
      world_position: toFreeCADPoint(event.point),
    });
  }

  return <primitive object={scene} onPointerDown={handlePointerDown} />;
}
