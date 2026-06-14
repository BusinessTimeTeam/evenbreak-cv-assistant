"""RAG Assistant Chat UI — a lightweight FastAPI app that proxies chat
messages to a DigitalOcean managed GenAI agent and serves a simple web
interface.

The agent endpoint and API key are supplied directly via the environment; the
app never calls the DO API to discover the endpoint or mint a key.

Environment variables (injected by terraform via App Platform, or from .env
locally):
    AGENT_ENDPOINT — OpenAI-compatible chat endpoint of the agent (required)
    AGENT_API_KEY  — Secret API key for the agent (required)
    AGENT_NAME     — Display name of the agent (optional)
"""

import logging
import os
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger("chat-ui")

app = FastAPI(title="RAG Assistant")

# The agent endpoint and key come straight from the environment. No discovery,
# no key generation.
AGENT_ENDPOINT = os.environ.get("AGENT_ENDPOINT")
AGENT_API_KEY = os.environ.get("AGENT_API_KEY")
AGENT_NAME = os.environ.get("AGENT_NAME", "RAG Assistant")

# Serve the static HTML chat page.
INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text()


def _require_config(endpoint, api_key):
    """Validate the required runtime configuration.

    Raises RuntimeError naming any missing variables. The app needs both the
    agent endpoint and its API key supplied via the environment.
    """
    missing = [
        name
        for name, value in (("AGENT_ENDPOINT", endpoint), ("AGENT_API_KEY", api_key))
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set AGENT_ENDPOINT and AGENT_API_KEY."
        )


@app.on_event("startup")
async def startup_event():
    _require_config(AGENT_ENDPOINT, AGENT_API_KEY)
    logger.info("Chat UI ready; proxying to agent endpoint %s", AGENT_ENDPOINT)


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the chat UI."""
    return INDEX_HTML.replace("{{AGENT_NAME}}", AGENT_NAME)


@app.get("/health")
async def health():
    return {"status": "ok", "agent_ready": AGENT_ENDPOINT is not None}


@app.post("/api/chat")
async def chat(request: Request):
    """Proxy a chat message to the managed agent and return the response."""
    if not AGENT_ENDPOINT or not AGENT_API_KEY:
        return JSONResponse(status_code=503, content={"error": "Agent not ready"})

    body = await request.json()
    message = body.get("message", "")
    history = body.get("history", [])

    # Build OpenAI-compatible messages array.
    messages = []
    for h in history:
        messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    messages.append({"role": "user", "content": message})

    headers = {
        "Authorization": f"Bearer {AGENT_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(AGENT_ENDPOINT, json={"messages": messages}, headers=headers)

    try:
        data = resp.json()
    except Exception:
        return JSONResponse(status_code=resp.status_code, content={"error": resp.text})

    # Extract the response text from OpenAI-compatible format.
    content = ""
    if "choices" in data and len(data["choices"]) > 0:
        content = data["choices"][0].get("message", {}).get("content", "")
    elif "detail" in data:
        content = f"Error: {data['detail']}"

    return JSONResponse(content={"content": content, "usage": data.get("usage")})
