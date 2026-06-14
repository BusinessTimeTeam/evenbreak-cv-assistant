"""RAG Assistant Chat UI — a lightweight FastAPI app that proxies chat
messages to a DigitalOcean managed GenAI agent and serves a simple web
interface.

The agent endpoint and API key are supplied directly via the environment; the
app never calls the DO API to discover the endpoint or mint a key.

Environment variables (injected by terraform via App Platform, or from .env
locally):
    AGENT_ENDPOINT — OpenAI-compatible chat endpoint of the agent (required)
    AGENT_API_KEY  — Secret API key for the agent (required)
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

AGENT_ENDPOINT = os.environ["AGENT_ENDPOINT"]
AGENT_API_KEY = os.environ["AGENT_API_KEY"]

INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(request: Request):
    """Proxy a chat message to the managed agent and return the response."""
    body = await request.json()
    message = body.get("message", "")
    history = body.get("history", [])

    # Build OpenAI-compatible messages array.
    messages = []
    for h in history:
        messages.append(
            {"role": h.get("role", "user"), "content": h.get("content", "")}
        )
    messages.append({"role": "user", "content": message})

    headers = {
        "Authorization": f"Bearer {AGENT_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            AGENT_ENDPOINT, json={"messages": messages}, headers=headers
        )

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
