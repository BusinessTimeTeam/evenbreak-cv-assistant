"""Prompt building: the system prompt, search-query rewriting, and the final
message list sent to the chat model.

Query rewriting replaces the managed agent's "sub-queries" retrieval: a small
model call turns the latest message (plus recent history and the CV) into a few
standalone search queries, so follow-ups like "what about my second job?" still
retrieve the right passages. If that call fails or returns anything unusable,
retrieval falls back to searching on the raw message.
"""

import json
import logging
import re
from pathlib import Path

from inference import InferenceConfig, InferenceError, chat
from retrieval import ScoredChunk

logger = logging.getLogger(__name__)

MAX_QUERIES = 4
HISTORY_TURNS = 6
CV_SNIPPET_CHARS = 2000

_REWRITE_INSTRUCTION = (
    "You write search queries for a knowledge base about CVs, employment and "
    "disability inclusion in the UK. Given the conversation, the user's CV (if "
    f"any) and their latest message, return up to {MAX_QUERIES} short, standalone "
    "search queries that would find the guidance needed to answer the latest "
    "message. Resolve references like 'that' or 'my second job' using the "
    "conversation and CV. Respond with only a JSON array of strings."
)

_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)


def load_system_prompt(path: Path = Path(__file__).parent / "prompts" / "system.md") -> str:
    return path.read_text().strip()


def _parse_queries(reply: str | None) -> list[str]:
    match = _JSON_ARRAY.search(reply or "")
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [q.strip() for q in parsed if isinstance(q, str) and q.strip()][:MAX_QUERIES]


async def rewrite_queries(
    config: InferenceConfig, message: str, history: list[dict], cv_text: str | None
) -> list[str]:
    parts = []
    if cv_text:
        parts.append(f"CV (excerpt):\n{cv_text[:CV_SNIPPET_CHARS]}")
    recent = history[-HISTORY_TURNS:]
    if recent:
        parts.append(
            "Conversation so far:\n"
            + "\n".join(f"{turn.get('role', 'user')}: {turn.get('content', '')}" for turn in recent)
        )
    parts.append(f"Latest message:\n{message}")
    messages = [
        {"role": "system", "content": _REWRITE_INSTRUCTION},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
    try:
        reply = await chat(config, messages, temperature=0, max_tokens=512)
    except InferenceError as exc:
        logger.warning("Query rewrite failed, searching on the raw message: %s", exc)
        return [message]
    queries = _parse_queries(reply)
    if not queries:
        logger.warning("Query rewrite returned no usable queries: %r", (reply or "")[:200])
        return [message]
    return queries


def context_message(passages: list[ScoredChunk]) -> dict:
    blocks = [f"--- Source: {p.source} ---\n{p.text}" for p in passages]
    return {
        "role": "user",
        "content": (
            "Relevant passages from Evenbreak's knowledge base for my next message. "
            "Use them where they help, and ignore any that don't apply.\n\n"
            + "\n\n".join(blocks)
        ),
    }


def build_messages(
    system_prompt: str,
    cv_message: dict | None,
    passages: list[ScoredChunk],
    history: list[dict],
    message: str,
) -> list[dict]:
    messages = [{"role": "system", "content": system_prompt}]
    if cv_message:
        messages.append(cv_message)
    messages.extend(history)
    # Passages go right before the message they were retrieved for, so in a long
    # conversation the model doesn't attach them to an older turn.
    if passages:
        messages.append(context_message(passages))
    messages.append({"role": "user", "content": message})
    return messages
