// Same-origin by default: in production the built app is served by
// core/api.py itself (see the StaticFiles mount in the patched api.py),
// so relative paths just work. In dev, vite.config.js proxies these.
const API_BASE = "";

export function getSessionId() {
  const params = new URLSearchParams(window.location.search);
  return params.get("session") || "local";
}

export function glbUrl(version) {
  // Cache-bust with a version token so the <img>/GLTFLoader actually
  // refetches instead of serving a stale cached GLB after an edit.
  return `${API_BASE}/api/state/glb?v=${version ?? Date.now()}`;
}

export async function postIntent(intent) {
  const res = await fetch(`${API_BASE}/api/intent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(intent),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Intent failed (${res.status}): ${detail}`);
  }
  return res.json();
}

export function sceneSocketUrl(sessionId) {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws/scene?session=${sessionId}`;
}
