"""FastAPI Gateway."""
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from core.agent import CADAgent
from core.adapters.interfaces import CADAdapter
import os
import tempfile
import importlib
import time
import json
from datetime import datetime
from pathlib import Path

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

# Enable CORS so the web frontend can hit the API from any origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = CADAgent(adapter=adapter)


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    telemetry: dict | None = None


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    # A3.2: Start wall-clock timer before handle_message
    start_time = time.time()

    # handle_message now returns (response_text, session_tools); take the text.
    reply, session_tools = agent.handle_message(request.message)

    # A3.2/A3.3: Stop timer and fetch telemetry
    duration = time.time() - start_time
    token_telemetry = agent.get_token_telemetry()
    total_tokens = token_telemetry.get("total_tokens", 0)
    # Steps = number of LLM calls for this turn
    steps = token_telemetry.get("llm_calls", 0)
    # A3.3: Get real RPC count from adapter
    rpc_count = agent.adapter.get_rpc_count()
    # Reset RPC counter for next turn
    agent.adapter.reset_rpc_count()

    # A3.2: Print compact console summary
    print(
        f"[TURN_SUMMARY] Steps: {steps} | Tokens: {total_tokens} | RPC Trips: {rpc_count} | Time: {duration:.3f}s")

    # A3.2/A3.3: Add telemetry to response
    telemetry = {
        "duration_seconds": duration,
        "steps": steps,
        "total_tokens": total_tokens,
        "rpc_trips": rpc_count,
        "token_telemetry": token_telemetry,
    }

    # A3.4: Persist turn telemetry to JSONL file (best-effort, never fails the request)
    try:
        runs_dir = Path("runs")
        runs_dir.mkdir(parents=True, exist_ok=True)
        log_path = runs_dir / "turns.jsonl"

        # Determine success/failure status from reply
        is_error = reply.startswith("Operation stopped") or reply.startswith(
            "LLM request failed") or reply.startswith("Operation incomplete")
        termination_reason = "success"
        if "deadline" in reply:
            termination_reason = "turn_deadline"
        elif "token ceiling" in reply:
            termination_reason = "token_ceiling"
        elif "max_steps" in reply or "maximum reasoning steps" in reply:
            termination_reason = "max_steps"
        elif "LLM provider unavailable" in reply:
            termination_reason = "llm_transient_exhausted"
        elif "LLM request failed" in reply:
            termination_reason = "llm_error"
        elif is_error:
            termination_reason = "error"

        record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "prompt": request.message,
            "steps": steps,
            "total_tokens": total_tokens,
            "rpc_trips": rpc_count,
            "duration_seconds": duration,
            "success": not is_error,
            "termination_reason": termination_reason,
        }

        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Logging failure must NOT cause /chat to fail
        pass

    return ChatResponse(reply=reply, telemetry=telemetry)


@app.get("/api/telemetry/turn")
async def get_turn_telemetry():
    """Return per-turn telemetry metrics from the last handle_message call."""
    return {
        "token_telemetry": agent.get_token_telemetry(),
        "context_telemetry": agent.get_context_telemetry(),
        "router_token_savings": agent.get_router_token_savings(),
    }


@app.get("/api/telemetry/reset")
async def reset_telemetry():
    """Reset telemetry accumulators."""
    agent._token_telemetry = {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_tokens": 0,
        "llm_calls": 0,
        "model": None,
        "provider": None,
        "per_step": [],
        "usage_unavailable_calls": 0,
        "ceiling_reached": False,
    }
    agent._context_telemetry = []
    agent._router_token_savings = []
    return {"status": "telemetry reset"}


@app.get("/api/state/obj")
async def get_model_obj():
    filepath = os.path.abspath("current_state.obj")
    try:
        # Trigger the adapter to export the file to the local disk
        # Use the existing agent instance's adapter
        agent.adapter.export_obj(filepath)

        # Check if file was actually created
        if not os.path.exists(filepath):
            return {"error": "OBJ file was not generated."}

        return FileResponse(filepath, filename="piecad_state.obj")
    except Exception as e:
        return {"error": f"Export failed: {str(e)}"}


@app.get("/api/state/model")
async def get_state_model():
    """Export the live 3D model state for the web viewer.

    Triggers the adapter's export command into a temporary system directory
    (preferring GLB/glTF, falling back to OBJ) and returns the generated file
    to the client as a FileResponse.
    """
    try:
        # Write into the system temp directory via Python's tempfile module.
        tmp_dir = tempfile.mkdtemp(prefix="piecad_state_")

        export_result = ""
        filepath = None
        # Prefer GLB for web viewers; the adapter's export_state_model falls
        # back to .obj automatically when the FreeCAD version lacks glTF export.
        for fmt, ext in (("glb", ".glb"), ("obj", ".obj")):
            candidate = os.path.join(tmp_dir, f"piecad_state{ext}")
            export_result = agent.adapter.export_state_model(candidate, fmt)
            # Parse the actually-written path out of the bridge's confirmation
            # message; fall back to scanning the temp dir.
            if os.path.exists(candidate):
                filepath = candidate
                break
            for token in export_result.replace("\\", " ").split():
                if token.endswith(ext) and os.path.exists(token):
                    filepath = token
                    break
            if filepath:
                break

        if not filepath or not os.path.exists(filepath):
            return {"error": f"Model export failed: {export_result}"}

        media_type = {
            ".glb": "model/gltf-binary",
            ".gltf": "model/gltf+json",
            ".obj": "text/plain",
        }.get(os.path.splitext(filepath)[1].lower(), "application/octet-stream")

        return FileResponse(
            filepath,
            filename=os.path.basename(filepath),
            media_type=media_type,
        )
    except Exception as e:
        return {"error": f"Export failed: {str(e)}"}


if __name__ == "__main__":
    uvicorn.run("core.api:app", host="127.0.0.1", port=8000, reload=True)
