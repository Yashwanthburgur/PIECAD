"""PieCAD Core Backend API."""

from typing import Any, Dict, List
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

from core.agent import CADAgent

app = FastAPI(title="PieCAD Core API")
agent = CADAgent()


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    tool_calls: List[Dict[str, Any]] = []


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    user_msg = request.message.strip()
    reply_text, tool_calls = agent.handle_message(user_msg)
    return ChatResponse(reply=reply_text, tool_calls=tool_calls)


if __name__ == "__main__":
    uvicorn.run("core.api:app", host="127.0.0.1", port=8000, reload=True)