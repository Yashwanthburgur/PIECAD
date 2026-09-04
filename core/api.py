"""FastAPI Gateway."""
from fastapi import FastAPI
from pydantic import BaseModel
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

if __name__ == "__main__":
    uvicorn.run("core.api:app", host="127.0.0.1", port=8000, reload=True)
