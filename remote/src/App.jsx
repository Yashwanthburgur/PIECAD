import { useCallback, useState } from "react";
import Viewer from "./components/Viewer.jsx";
import IntentBar from "./components/IntentBar.jsx";
import { getSessionId, glbUrl, postIntent } from "./lib/api.js";
import { useSceneSocket } from "./hooks/useSceneSocket.js";

export default function App() {
  const sessionId = getSessionId();
  const [version, setVersion] = useState(() => Date.now());
  const [selection, setSelection] = useState(null);
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState("");

  const handleSceneUpdate = useCallback((newVersion) => {
    setVersion(newVersion ?? Date.now());
    setSelection(null); // stale reference to a mesh that may no longer exist
  }, []);

  useSceneSocket(sessionId, handleSceneUpdate);

  async function handleSend(text) {
    setSending(true);
    setStatus("");
    try {
      const result = await postIntent({
        session_id: sessionId,
        cad_object_name: selection.cad_object_name,
        world_position: selection.world_position,
        text,
      });
      setStatus(result.reply ?? "Done.");
    } catch (err) {
      setStatus(err.message);
    } finally {
      setSending(false);
    }
  }

  return (
    <div style={{ position: "fixed", inset: 0, background: "#111" }}>
      <Viewer glbUrl={glbUrl(version)} onSelect={setSelection} />
      {status && <div style={statusStyle}>{status}</div>}
      <IntentBar selection={selection} onSend={handleSend} sending={sending} />
    </div>
  );
}

const statusStyle = {
  position: "fixed", top: 10, left: 10, right: 10,
  padding: "8px 12px", background: "rgba(0,0,0,0.7)", color: "#8f8",
  borderRadius: 8, fontSize: 12, fontFamily: "sans-serif",
};
