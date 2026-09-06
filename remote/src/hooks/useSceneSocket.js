import { useEffect, useRef } from "react";
import { sceneSocketUrl } from "../lib/api.js";

// This is the "WebSocket is for notification, not streaming" piece from
// the design doc: the server pushes a tiny {type:"scene_updated"} ping
// whenever the CAD state changes, and the viewer reacts by refetching the
// GLB -- it never receives geometry over this socket.
export function useSceneSocket(sessionId, onUpdate) {
  const wsRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    let retryTimer = null;

    function connect() {
      const ws = new WebSocket(sceneSocketUrl(sessionId));
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "scene_updated") onUpdate(msg.version);
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        if (!cancelled) retryTimer = setTimeout(connect, 2000);
      };

      ws.onerror = () => ws.close();
    }

    connect();
    return () => {
      cancelled = true;
      clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, [sessionId, onUpdate]);
}
