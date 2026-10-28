"""RAG Assistant Chat UI — a lightweight FastAPI app that answers chat messages
using Evenbreak's knowledge base and DigitalOcean serverless inference, and
serves a simple web interface.

Each chat turn: rewrite the conversation into search queries, embed them,
search the in-memory knowledge-base index, then send the system prompt, CV,
retrieved passages, history and message to the chat model.

Environment variables (injected by terraform via App Platform, or from .env
locally):
    MODEL_ACCESS_KEY   — DO model access key for serverless inference (required)
    HISTORY_SIGNING_KEY — secret that signs conversation history (required)
    INFERENCE_BASE_URL — default https://inference.do-ai.run/v1
    CHAT_MODEL         — default openai-gpt-oss-20b
    EMBEDDING_MODEL    — default qwen3-embedding-0.6b; must match the index
    INDEX_PATH         — built index file, default /index/kb_index.npz
    RETRIEVAL_K        — passages retrieved per turn, default 10
"""

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from extraction import extract_text
from history_signing import HistoryTokenError, sign_history, verify_history
from inference import InferenceConfig, InferenceError, chat, embed
from prompting import build_messages, load_system_prompt, rewrite_queries
from retrieval import Index, ScoredChunk

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger("chat-ui")

_config = InferenceConfig.from_env()
SYSTEM_PROMPT = load_system_prompt()
HISTORY_SIGNING_KEY = os.environ["HISTORY_SIGNING_KEY"].encode()
EXPIRED_MESSAGE = "This conversation has expired. Please start a new one."
RETRIEVAL_K = int(os.environ.get("RETRIEVAL_K", "10"))
DEFAULT_INDEX_PATH = "/index/kb_index.npz"

# Loaded once at startup (see lifespan); read-only afterwards.
_index: Index | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _index
    path = Path(os.environ.get("INDEX_PATH", DEFAULT_INDEX_PATH))
    # Raises IndexLoadError (missing / unreadable / wrong embedding model),
    # which aborts startup with a message saying how to rebuild the index.
    _index = Index.load(path, expected_embedding_model=_config.embedding_model)
    logger.info("Loaded knowledge-base index: %d chunks from %s", len(_index), path)
    yield


app = FastAPI(title="RAG Assistant", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

INDEX_HTML_PATH = STATIC_DIR / "index.html"

# Uploaded CVs are kept server-side keyed by a session id, so the browser only
# has to hold the session id and the Q&A turns — not the full CV text. The CV is
# re-injected as a stable leading message on every model call (the model is
# stateless and has no memory of its own).
#
# NOTE: this is an in-process dict. It is fine for the current single-instance
# App Platform deployment, but is lost on restart and not shared across
# instances. Move it to a shared store (e.g. Redis/Spaces) if the app scales out.
_cv_sessions: dict[str, dict] = {}


def _cv_message(cv: dict) -> dict:
    """Build the stable leading message that carries an uploaded CV.

    Uses the ``user`` role: the system message carries only the assistant's
    instructions (prompts/system.md), and the CV is user-supplied content.
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


async def _answer(
    message: str, history: list[dict], cv: dict | None
) -> tuple[str, list[ScoredChunk]]:
    """Retrieve knowledge-base passages for this turn and ask the chat model.

    Raises InferenceError if embedding or the chat call fails; query rewriting
    falls back to the raw message on its own.
    """
    queries = await rewrite_queries(_config, message, history, cv["text"] if cv else None)
    passages = _index.search(await embed(_config, queries), k=RETRIEVAL_K)
    messages = build_messages(
        SYSTEM_PROMPT, _cv_message(cv) if cv else None, passages, history, message
    )
    return await chat(_config, messages), passages


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    """Answer a chat message from the knowledge base and return the reply.

    If the request carries a session id with a stored CV, that CV is included
    on every turn so the model can reason about it. ``sources`` lists the
    retrieved knowledge-base files (the UI ignores it; the comparison uses it).

    The browser sends back the history and ``history_token`` from the previous
    response; history that fails verification is rejected with a 400. The
    response carries the updated history and a fresh token.
    """
    body = await request.json()
    message = body.get("message", "")
    received = body.get("history", [])
    history_token = body.get("history_token")
    session_id = body.get("session_id")

    # Verify the history exactly as received, before anything reads it.
    try:
        verify_history(HISTORY_SIGNING_KEY, received, session_id, history_token)
    except HistoryTokenError as exc:
        logger.info("Rejected chat history: %s", exc)
        return JSONResponse(
            status_code=400, content={"error": EXPIRED_MESSAGE, "expired": True}
        )

    history = [
        {"role": h.get("role", "user"), "content": h.get("content", "")} for h in received
    ]
    cv = _cv_sessions.get(session_id) if session_id else None

    try:
        content, passages = await _answer(message, history, cv)
    except InferenceError as exc:
        logger.warning("Chat turn failed: %s", exc)
        # The failed turn isn't added, so the browser keeps its verified history.
        return JSONResponse(
            content={
                "content": f"Error: {exc}",
                "sources": [],
                "history": received,
                "history_token": history_token,
            }
        )
    updated = received + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": content},
    ]
    return JSONResponse(
        content={
            "content": content,
            "sources": [{"source": p.source, "score": p.score} for p in passages],
            "history": updated,
            "history_token": sign_history(HISTORY_SIGNING_KEY, updated, session_id),
        }
    )


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
):
    """Accept an uploaded CV (PDF / Word), extract its text, store it against a
    new session id, and return readiness metadata without calling the model."""
    data = await file.read()
    try:
        cv_text = extract_text(file.filename or "", data)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    # Upload starts a fresh CV conversation. The first model call is made only
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
