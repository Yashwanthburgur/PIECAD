"""FastAPI Gateway."""
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import FileResponse
import uvicorn
from core.agent import CADAgent
from core.adapters.interfaces import CADAdapter
import os
import importlib

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


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    return ChatResponse(reply=agent.handle_message(request.message))


@app.get("/api/state/glb")
async def get_model_glb():
    filepath = os.path.abspath("current_state.glb")
    try:
        # Trigger the adapter to export the file to the local disk
        # Use the existing agent instance's adapter
        agent.adapter.export_glb(filepath)

        # Check if file was actually created
        if not os.path.exists(filepath):
            return {"error": "GLB file was not generated."}

        return FileResponse(filepath, media_type="model/gltf-binary", filename="piecad_state.glb")
    except Exception as e:
        return {"error": f"Export failed: {str(e)}"}


if __name__ == "__main__":
    uvicorn.run("core.api:app", host="127.0.0.1", port=8000, reload=True)
