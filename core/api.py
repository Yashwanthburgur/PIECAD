"""FastAPI Gateway."""
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from core.agent import CADAgent
from adapters.freecad.adapter import FreeCADAdapter

app = FastAPI(title="PieCAD Core API")
agent = CADAgent(adapter=FreeCADAdapter(port=9876))

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    reply: str

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    return ChatResponse(reply=agent.handle_message(request.message))

if __name__ == "__main__":
    uvicorn.run("core.api:app", host="127.0.0.1", port=8000, reload=True)