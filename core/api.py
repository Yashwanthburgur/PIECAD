"""PieCAD Core Backend API."""

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="PieCAD Core API")


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    user_msg = request.message.strip()
    
    # Simple echo / pipeline verification for now
    # We will attach the agent & LLM orchestrator in the next step
    return ChatResponse(
        reply=f"Backend received: '{user_msg}'. Ready to route to Agent orchestrator."
    )


if __name__ == "__main__":
    uvicorn.run("core.api:app", host="127.0.0.1", port=8000, reload=True)