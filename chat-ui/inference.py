"""Client for DigitalOcean serverless inference: chat completions and embeddings.

Both endpoints are OpenAI-compatible and authenticated with a model access key
(``MODEL_ACCESS_KEY``). Every failure — HTTP error status, timeout, or a
response that doesn't have the expected shape — is raised as InferenceError so
callers have one exception type to handle.
"""

import os
from dataclasses import dataclass

import httpx

import trial

TIMEOUT_SECONDS = 120.0


class InferenceError(Exception):
    """A serverless inference request failed or returned something unusable."""


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
        return resp.json()
    except ValueError as exc:
        raise InferenceError(f"{path} returned non-JSON: {resp.text[:500]}") from exc


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
    data = await _post(config, "chat/completions", payload, client)
    trial.record_usage(payload["model"], data.get("usage"))
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise InferenceError(f"chat response malformed: {str(data)[:500]}") from exc
    return content or ""
