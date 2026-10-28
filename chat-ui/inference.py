"""Client for DigitalOcean serverless inference: chat completions (plain and
streamed) and embeddings.

Both endpoints are OpenAI-compatible and authenticated with a model access key
(``MODEL_ACCESS_KEY``). Every failure — HTTP error status, timeout, or a
response that doesn't have the expected shape — is raised as InferenceError so
callers have one exception type to handle.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

import httpx

import trial

TIMEOUT_SECONDS = 120.0
IDLE_TIMEOUT_SECONDS = 60.0


class InferenceError(Exception):
    """A serverless inference request failed or returned something unusable."""


@dataclass(frozen=True)
class ChatDelta:
    """A piece of a streamed reply: the model thinking, or the answer itself."""

    kind: Literal["thinking", "answer"]
    text: str


@dataclass(frozen=True)
class InferenceConfig:
    api_key: str
    base_url: str = "https://inference.do-ai.run/v1"
    chat_model: str = "openai-gpt-oss-20b"
    embedding_model: str = "qwen3-embedding-0.6b"

    @classmethod
    def from_env(cls) -> "InferenceConfig":
        defaults = cls(api_key="")
        return cls(
            api_key=os.environ["MODEL_ACCESS_KEY"],
            base_url=os.environ.get("INFERENCE_BASE_URL", defaults.base_url),
            chat_model=os.environ.get("CHAT_MODEL", defaults.chat_model),
            embedding_model=os.environ.get("EMBEDDING_MODEL", defaults.embedding_model),
        )


async def _post(
    config: InferenceConfig, path: str, payload: dict, client: httpx.AsyncClient | None
) -> dict:
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    url = f"{config.base_url.rstrip('/')}/{path}"
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as owned:
                resp = await owned.post(url, json=payload, headers=headers)
        else:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise InferenceError(f"{path} request failed: {exc!r}") from exc

    if resp.status_code >= 400:
        raise InferenceError(f"{path} returned {resp.status_code}: {resp.text[:500]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise InferenceError(f"{path} returned non-JSON: {resp.text[:500]}") from exc
    if not isinstance(data, dict):
        raise InferenceError(f"{path} returned a non-object: {resp.text[:500]}")
    return data


async def embed(
    config: InferenceConfig,
    texts: list[str],
    *,
    batch_size: int = 64,
    client: httpx.AsyncClient | None = None,
) -> list[list[float]]:
    """Embed texts with the configured embedding model, preserving input order."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        data = await _post(
            config,
            "embeddings",
            {"model": config.embedding_model, "input": batch, "encoding_format": "float"},
            client,
        )
        trial.record_usage(config.embedding_model, data.get("usage"))
        try:
            items = sorted(data["data"], key=lambda item: item["index"])
            batch_vectors = [item["embedding"] for item in items]
        except (KeyError, TypeError) as exc:
            raise InferenceError(f"embeddings response malformed: {exc!r}") from exc
        if len(batch_vectors) != len(batch):
            raise InferenceError(
                f"embeddings returned {len(batch_vectors)} vectors for {len(batch)} inputs"
            )
        vectors.extend(batch_vectors)
    return vectors


def _chat_payload(
    config: InferenceConfig,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    top_p: float,
    model: str | None,
    params: dict | None,
) -> dict:
    payload = {
        "model": model or config.chat_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "top_p": top_p,
    }
    for key, value in (params or {}).items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    return payload


async def chat(
    config: InferenceConfig,
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    top_p: float = 1.0,
    model: str | None = None,
    params: dict | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Run a chat completion and return the first choice's message content.

    ``params`` is merged over the request last, so a model can override any
    setting; a key set to None is removed (some models reject ``top_p`` or want
    ``max_completion_tokens`` instead of ``max_tokens``).
    """
    payload = _chat_payload(config, messages, temperature, max_tokens, top_p, model, params)
    data = await _post(config, "chat/completions", payload, client)
    trial.record_usage(payload["model"], data.get("usage"))
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise InferenceError(f"chat response malformed: {str(data)[:500]}") from exc
    reason = choice.get("finish_reason")
    if reason and reason != "stop":
        raise InferenceError(f"chat finished with {reason}")
    return content or ""


async def chat_stream(
    config: InferenceConfig,
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    top_p: float = 1.0,
    model: str | None = None,
    params: dict | None = None,
    client: httpx.AsyncClient | None = None,
    idle_timeout: float = IDLE_TIMEOUT_SECONDS,
) -> AsyncIterator[ChatDelta]:
    """Stream a chat completion, yielding thinking and answer pieces as they arrive.

    The request is built like ``chat``'s. The stream fails with InferenceError if
    the provider goes silent for ``idle_timeout`` seconds, ends without a
    ``stop`` finish reason, or produces no answer text. Usage is recorded once
    the stream ends, before any such failure is raised.
    """
    payload = _chat_payload(config, messages, temperature, max_tokens, top_p, model, params)
    payload["stream"] = True
    payload["stream_options"] = {"include_usage": True}
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    owned = client is None
    if owned:
        client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=idle_timeout))
    usage = None
    reason = None
    answered = False
    try:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode(errors="replace")
                raise InferenceError(f"chat/completions returned {resp.status_code}: {body[:500]}")
            lines = resp.aiter_lines().__aiter__()
            while True:
                try:
                    line = await asyncio.wait_for(lines.__anext__(), idle_timeout)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as exc:
                    raise InferenceError(f"chat stream idle for {idle_timeout}s") from exc
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError as exc:
                    raise InferenceError("chat stream sent a line that is not JSON") from exc
                if not isinstance(chunk, dict):
                    raise InferenceError("chat stream sent a chunk that is not an object")
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    if choice.get("finish_reason"):
                        reason = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    if delta.get("reasoning_content"):
                        yield ChatDelta("thinking", delta["reasoning_content"])
                    if delta.get("content"):
                        answered = True
                        yield ChatDelta("answer", delta["content"])
    except httpx.HTTPError as exc:
        raise InferenceError(f"chat/completions request failed: {exc!r}") from exc
    finally:
        if owned:
            await client.aclose()

    trial.record_usage(payload["model"], usage)
    if reason is None:
        raise InferenceError("chat stream ended without a finish reason")
    if reason != "stop":
        raise InferenceError(f"chat finished with {reason}")
    if not answered:
        raise InferenceError("chat stream had no answer text")
