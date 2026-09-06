import { Suspense } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import SceneModel from "./SceneModel.jsx";

export default function Viewer({ glbUrl, onSelect }) {
  return (
    <Canvas camera={{ position: [200, 200, 200], fov: 50 }} style={{ touchAction: "none" }}>
      <ambientLight intensity={0.7} />
      <directionalLight position={[100, 200, 100]} intensity={0.9} />
      <Suspense fallback={null}>
        <SceneModel key={glbUrl} url={glbUrl} onSelect={onSelect} />
      </Suspense>
      <OrbitControls makeDefault enableDamping dampingFactor={0.1} />
    </Canvas>
  );
}
