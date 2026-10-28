"""Tests for the DO serverless inference client (chat + embeddings).

Network calls go through httpx.MockTransport so these tests stay offline.
"""

import asyncio
import json

import httpx
import pytest

import inference
import trial
from inference import InferenceConfig, InferenceError

CONFIG = InferenceConfig(api_key="k")


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _embedding_response(request, *, drop=0, reverse=False):
    texts = json.loads(request.content)["input"]
    data = [{"index": i, "embedding": [float(len(t)), float(i)]} for i, t in enumerate(texts)]
    if reverse:
        data.reverse()
    if drop:
        data = data[:-drop]
    return httpx.Response(200, json={"data": data})


def test_embed_sends_model_and_bearer_key():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return _embedding_response(request)

    asyncio.run(inference.embed(CONFIG, ["hello"], client=_client(handler)))

    assert seen["url"].endswith("/embeddings")
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "qwen3-embedding-0.6b"
    assert seen["body"]["input"] == ["hello"]


def test_embed_batches_and_preserves_order():
    batches = []

    def handler(request):
        batches.append(json.loads(request.content)["input"])
        return _embedding_response(request, reverse=True)

    texts = ["a", "bb", "ccc", "dddd", "eeeee"]
    vectors = asyncio.run(
        inference.embed(CONFIG, texts, batch_size=2, client=_client(handler))
    )

    assert batches == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    # First component encodes the text length, so order is checkable.
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_embed_raises_when_count_mismatches():
    def handler(request):
        return _embedding_response(request, drop=1)

    with pytest.raises(InferenceError):
        asyncio.run(inference.embed(CONFIG, ["a", "b"], client=_client(handler)))


def test_chat_returns_first_choice_content():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
        )

    messages = [{"role": "user", "content": "hello"}]
    reply = asyncio.run(inference.chat(CONFIG, messages, client=_client(handler)))

    assert reply == "hi"
    assert seen["url"].endswith("/chat/completions")
    assert seen["body"]["model"] == "openai-gpt-oss-20b"
    assert seen["body"]["messages"] == messages
    assert seen["body"]["temperature"] == 0.2
    assert seen["body"]["max_tokens"] == 2048
    assert seen["body"]["top_p"] == 1.0


def test_chat_raises_inference_error_on_http_error():
    def handler(request):
        return httpx.Response(429, json={"error": "Too Many Requests"})

    with pytest.raises(InferenceError, match="429"):
        asyncio.run(
            inference.chat(CONFIG, [{"role": "user", "content": "x"}], client=_client(handler))
        )


def test_chat_raises_inference_error_on_malformed_response():
    def handler(request):
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(InferenceError):
        asyncio.run(
            inference.chat(CONFIG, [{"role": "user", "content": "x"}], client=_client(handler))
        )


def test_config_from_env_requires_model_access_key(monkeypatch):
    for name in ("MODEL_ACCESS_KEY", "INFERENCE_BASE_URL", "CHAT_MODEL", "EMBEDDING_MODEL"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises((KeyError, InferenceError)):
        InferenceConfig.from_env()

    monkeypatch.setenv("MODEL_ACCESS_KEY", "secret")
    config = InferenceConfig.from_env()

    assert config.api_key == "secret"
    assert config.base_url == "https://inference.do-ai.run/v1"
    assert config.chat_model == "openai-gpt-oss-20b"
    assert config.embedding_model == "qwen3-embedding-0.6b"


def _chat_response(usage=None):
    body = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def test_chat_records_usage_in_active_ledger():
    ledger = trial.Ledger()
    handler = lambda request: _chat_response({"prompt_tokens": 12, "completion_tokens": 3})

    async def run():
        with trial.recording(ledger), trial.purpose("reply"):
            await inference.chat(CONFIG, [{"role": "user", "content": "x"}], client=_client(handler))

    asyncio.run(run())
    assert ledger.tokens("reply") == (12, 3)


def test_embed_records_prompt_tokens():
    ledger = trial.Ledger()

    def handler(request):
        response = _embedding_response(request)
        body = json.loads(response.content)
        body["usage"] = {"prompt_tokens": 7, "total_tokens": 7}
        return httpx.Response(200, json=body)

    async def run():
        with trial.recording(ledger), trial.purpose("embed"):
            await inference.embed(CONFIG, ["a", "b", "c"], batch_size=2, client=_client(handler))

    asyncio.run(run())
    assert ledger.tokens("embed") == (14, 0)  # two batches


def test_chat_without_usage_still_returns_content():
    ledger = trial.Ledger()

    async def run():
        with trial.recording(ledger):
            return await inference.chat(CONFIG, [], client=_client(lambda r: _chat_response()))

    assert asyncio.run(run()) == "hi"
    assert ledger.microusd() == 0


def test_chat_params_override_and_remove_keys():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _chat_response()

    asyncio.run(
        inference.chat(
            CONFIG,
            [],
            params={"top_p": None, "max_tokens": None, "max_completion_tokens": 8192},
            client=_client(handler),
        )
    )
    assert "top_p" not in seen["body"]
    assert "max_tokens" not in seen["body"]
    assert seen["body"]["max_completion_tokens"] == 8192
    assert seen["body"]["temperature"] == 0.2


def test_chat_with_non_object_json_raises_inference_error():
    with pytest.raises(InferenceError):
        asyncio.run(inference.chat(CONFIG, [], client=_client(lambda r: httpx.Response(200, json=["x"]))))


def test_chat_with_malformed_usage_still_returns_content():
    ledger = trial.Ledger()

    async def run():
        with trial.recording(ledger):
            return await inference.chat(CONFIG, [], client=_client(lambda r: _chat_response("weird")))

    assert asyncio.run(run()) == "hi"
    assert ledger.microusd() == 0
