"""FastAPI Gateway."""
import base64
import importlib
import io
import json
import math
import os
import socket
import uuid

import qrcode
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.adapters.interfaces import CADAdapter
from core.agent import CADAgent
from core.contracts.ir import RemoteIntent

ACTIVE_CAD_ADAPTER = os.getenv("ACTIVE_CAD_ADAPTER", "freecad")
ADAPTER_FACTORY = {
    "freecad": "adapters.freecad.adapter.FreeCADAdapter"
}

# Dynamically load the adapter class
module_path, class_name = ADAPTER_FACTORY[ACTIVE_CAD_ADAPTER].rsplit(".", 1)
_mod = importlib.import_module(module_path)
_AdapterClass = getattr(_mod, class_name)

# Instantiate the adapter
adapter = _AdapterClass(port=9876)
app = FastAPI(title="PieCAD Core API")

agent = CADAgent(adapter=adapter)


# --------------------------------------------------------------------------- #
# PieCAD Remote support.
#
# Everything below this line is transport/session plumbing for the mobile
# viewer. Deliberately kept out of core/agent.py: the orchestrator stays
# CAD-agnostic AND transport-agnostic -- it has no idea a phone, a
# WebSocket, or a QR code exists. This file is the one layer allowed to
# know about that.
# --------------------------------------------------------------------------- #

class ConnectionManager:
    """In-memory per-session WebSocket registry.

    Fine for the Phase 1 target (one desktop + one paired phone). If you
    later need multiple simultaneous paired devices or restart-survival,
    swap this for a small pub/sub (e.g. Redis) without touching anything
    else in this file's public surface.
    """

    def __init__(self):
        self._sockets: dict[str, list[WebSocket]] = {}

    async def connect(self, session_id: str, ws: WebSocket):
        await ws.accept()
        self._sockets.setdefault(session_id, []).append(ws)

    def disconnect(self, session_id: str, ws: WebSocket):
        conns = self._sockets.get(session_id, [])
        if ws in conns:
            conns.remove(ws)

    async def broadcast(self, session_id: str, message: dict):
        for ws in list(self._sockets.get(session_id, [])):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(session_id, ws)


manager = ConnectionManager()
_sessions: dict[str, dict] = {}


def _lan_ip() -> str:
    """Best-effort guess at this machine's LAN IP, for the QR code URL.

    Doesn't actually send any traffic to 8.8.8.8 -- opening a UDP socket
    and calling connect() just makes the OS pick a local interface/IP,
    which is all we need here.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


@app.post("/api/session")
async def create_session():
    """Start a temporary PieCAD Remote pairing session and return a QR code.

    The QR encodes a URL to the built mobile viewer (served at /remote,
    see the StaticFiles mount below) with the session id attached. Scanning
    it and connecting is the entire "pairing" flow for local Wi-Fi -- no
    external relay server needed for this case.
    """
    session_id = uuid.uuid4().hex[:8]
    _sessions[session_id] = {"created": True}

    url = f"http://{_lan_ip()}:8000/remote/?session={session_id}"

    qr_img = qrcode.make(url)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return {"session_id": session_id, "url": url, "qr_png_base64": qr_b64}


@app.websocket("/ws/scene")
async def scene_socket(websocket: WebSocket):
    """Push-only channel: notifies the viewer a new scene is available.

    This is intentionally NOT used to stream geometry -- see the master
    context's note that WebSocket here means "publish that a new scene is
    available", not continuous model streaming. The viewer reacts to a
    ping by refetching /api/state/glb.
    """
    session_id = websocket.query_params.get("session", "local")
    await manager.connect(session_id, websocket)
    try:
        while True:
            # No inbound messages expected on this channel; just keep it
            # open. The client's own reconnect logic handles drops.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)


async def _notify_scene_updated(session_id: str = "local"):
    await manager.broadcast(session_id, {"type": "scene_updated", "version": uuid.uuid4().hex})


def _resolve_nearest_face(cad_object_name: str, world_position) -> str | None:
    """Turn a raw 3D touch point into a stable FreeCAD face reference.

    Deliberately reuses the adapter's EXISTING `get_faces` tool instead of
    baking per-triangle metadata into the GLB export: the viewer already
    tells us WHICH object was touched (GLB node name == FreeCAD object
    name, since FreeCAD's exporter names meshes after their source
    object), so we only need to pick the closest face on that one object.
    No new FreeCAD-side code required for this.
    """
    try:
        raw = adapter.execute_command("get_faces", object_name=cad_object_name)
        faces = json.loads(raw)
    except Exception:
        return None

    if not faces:
        return None

    def dist(face):
        c = face["center"]
        return math.dist(
            (c["x"], c["y"], c["z"]),
            (world_position.x, world_position.y, world_position.z),
        )

    nearest = min(faces, key=dist)
    return nearest["face_id"]


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


@app.post("/api/intent", response_model=ChatResponse)
async def intent_endpoint(intent: RemoteIntent):
    """Entry point for PieCAD Remote (mobile viewer).

    The viewer supplies WHERE (object + exact point) and the raw
    utterance. This endpoint resolves WHERE into a face reference and
    folds everything into a single enriched natural-language message,
    then hands off to the SAME CADAgent the desktop panel uses. The agent
    still decides WHAT operation the instruction means -- this endpoint
    performs no CAD logic itself, matching the "viewer captures where;
    backend/CAD logic decides what" boundary from the design.
    """
    face_ref = _resolve_nearest_face(intent.cad_object_name, intent.world_position)
    p = intent.world_position

    if face_ref:
        enriched = (
            f"On object '{intent.cad_object_name}', touched face '{face_ref}' "
            f"at approximately ({p.x:.2f}, {p.y:.2f}, {p.z:.2f}) mm: {intent.text}"
        )
    else:
        enriched = (
            f"On object '{intent.cad_object_name}' near "
            f"({p.x:.2f}, {p.y:.2f}, {p.z:.2f}) mm: {intent.text}"
        )

    reply = agent.handle_message(enriched)
    await _notify_scene_updated(intent.session_id)
    return ChatResponse(reply=reply)


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    reply = agent.handle_message(request.message)
    await _notify_scene_updated()  # also wake up any paired phone
    return ChatResponse(reply=reply)


@app.get("/api/state/glb")
async def get_model_glb():
    filepath = os.path.abspath("current_state.glb")
    try:
        # Trigger the adapter to export the file to the local disk
        agent.adapter.export_glb(filepath)
    except Exception as e:
        # IMPORTANT: raise, don't return {"error": ...} with a 200 status.
        # A 200 response body that isn't valid GLB gets handed straight to
        # THREE.GLTFLoader on the client, which fails with a confusing
        # "Unsupported asset" error instead of surfacing the real problem.
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    if not os.path.exists(filepath):
        raise HTTPException(status_code=500, detail="GLB file was not generated.")

    return FileResponse(filepath, media_type="model/gltf-binary", filename="piecad_state.glb")


# Serve the built PieCAD Remote web app after `npm run build` in remote/.
# Same-origin serving means the mobile viewer never needs CORS handling.
_remote_dist = os.path.join(os.path.dirname(__file__), "..", "remote", "dist")
if os.path.isdir(_remote_dist):
    app.mount("/remote", StaticFiles(directory=_remote_dist, html=True), name="remote")


if __name__ == "__main__":
    # NOTE: host changed from 127.0.0.1 to 0.0.0.0 -- a phone on the same
    # Wi-Fi network cannot reach a server bound only to loopback.
    uvicorn.run("core.api:app", host="0.0.0.0", port=8000, reload=True)