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
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from extraction import extract_text

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger("chat-ui")

app = FastAPI(title="RAG Assistant")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

AGENT_ENDPOINT = os.environ["AGENT_ENDPOINT"]
AGENT_API_KEY = os.environ["AGENT_API_KEY"]

INDEX_HTML_PATH = STATIC_DIR / "index.html"

# Uploaded CVs are kept server-side keyed by a session id, so the browser only
# has to hold the session id and the Q&A turns — not the full CV text. The CV is
# re-injected as a stable leading message on every agent call (the agent is
# stateless and has no memory of its own).
#
# NOTE: this is an in-process dict. It is fine for the current single-instance
# App Platform deployment, but is lost on restart and not shared across
# instances. Move it to a shared store (e.g. Redis/Spaces) if the app scales out.
_cv_sessions: dict[str, dict] = {}


def _cv_message(cv: dict) -> dict:
    """Build the stable leading message that carries an uploaded CV.

    Uses the ``user`` role, not ``system``: the DO managed agent rejects
    client-supplied system/developer messages ("agent instructions are set via
    agent configuration", HTTP 400). Agent instructions live in the agent config.
    """
    return {
        "role": "user",
        "content": (
            "I have uploaded the following CV for discussion. Refer to it "
            f"when answering.\n\n--- CV: {cv['filename']} ---\n{cv['text']}"
        ),
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    # Read on request so frontend edits are visible immediately during local
    # development. Uvicorn's reload watcher only restarts for Python changes.
    return INDEX_HTML_PATH.read_text()


@app.get("/health")
async def health():
    return {"status": "ok"}


async def _call_agent(messages):
    """Send an OpenAI-compatible chat request to the managed agent and return
    the assistant's reply text."""
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
        return f"Error: agent returned {resp.status_code}: {resp.text[:500]}"

    if "choices" in data and len(data["choices"]) > 0:
        return data["choices"][0].get("message", {}).get("content", "")
    if "detail" in data:
        return f"Error: {data['detail']}"
    return ""


@app.post("/api/chat")
async def chat(request: Request):
    """Proxy a chat message to the managed agent and return the response.

    If the request carries a session id with a stored CV, that CV is prepended
    as a stable leading message so the agent can reason about it on every turn.
    """
    body = await request.json()
    message = body.get("message", "")
    history = body.get("history", [])
    session_id = body.get("session_id")

    # Build OpenAI-compatible messages array, CV (if any) first.
    messages = []
    cv = _cv_sessions.get(session_id) if session_id else None
    if cv:
        messages.append(_cv_message(cv))
    for h in history:
        messages.append(
            {"role": h.get("role", "user"), "content": h.get("content", "")}
        )
    messages.append({"role": "user", "content": message})

    content = await _call_agent(messages)
    return JSONResponse(content={"content": content})


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
):
    """Accept an uploaded CV (PDF / Word), extract its text, store it against a
    new session id, and return readiness metadata without calling the agent."""
    data = await file.read()
    try:
        cv_text = extract_text(file.filename or "", data)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    # Upload starts a fresh CV conversation. The first agent call is made only
    # after the user selects a suggested action or asks their own question.
    session_id = str(uuid.uuid4())
    cv = {"filename": file.filename, "text": cv_text}
    _cv_sessions[session_id] = cv

    return JSONResponse(
        content={
            "filename": file.filename,
            "session_id": session_id,
            "status": "ready",
        }
    )
