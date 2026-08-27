"""PieCAD Core Backend API."""

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

from core.agent import CADAgent
from adapters.freecad.adapter import FreeCADAdapter

app = FastAPI(title="PieCAD Core API")

# Dependency Injection: Wiring the FreeCAD Adapter to the Generic Agent
freecad_adapter = FreeCADAdapter(port=9876)
agent = CADAgent(adapter=freecad_adapter)

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    reply: str

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    reply_text = agent.handle_message(request.message)
    return ChatResponse(reply=reply_text)

if __name__ == "__main__":
    uvicorn.run("core.api:app", host="127.0.0.1", port=8000, reload=True)