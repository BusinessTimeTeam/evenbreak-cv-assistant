"""Tests for the /api/chat answer pipeline: rewrite → embed → search → chat.

Network calls (rewrite, embed, chat) are stubbed on ``main``; the index is a
real, tiny in-memory Index.
"""

import os

import numpy as np
import pytest

os.environ.setdefault("MODEL_ACCESS_KEY", "sk-test")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from chunking import Chunk  # noqa: E402
from inference import InferenceError  # noqa: E402
from retrieval import Index, IndexLoadError  # noqa: E402

ADJUSTMENTS = "Ask for reasonable adjustments before the interview."
GAPS = "Keep explanations of CV gaps brief and positive."


@pytest.fixture
def pipeline(monkeypatch):
    calls = {"embed": [], "chat": []}
    index = Index(
        [Chunk("guides/adjustments.md", ADJUSTMENTS), Chunk("course/gaps.md", GAPS)],
        np.array([[1, 0], [0, 1]], dtype=np.float32),
        embedding_model="qwen3-embedding-0.6b",
        source_hash="h",
        built_at="2026-10-28T00:00:00+00:00",
    )

    async def fake_rewrite(config, message, history, cv_text):
        return calls.get("queries", [message])

    async def fake_embed(config, texts, **kwargs):
        calls["embed"].append(texts)
        if "embed_error" in calls:
            raise calls["embed_error"]
        return [[1.0, 0.0] for _ in texts]  # every query matches the adjustments chunk

    async def fake_chat(config, messages, **kwargs):
        calls["chat"].append(messages)
        if "chat_error" in calls:
            raise calls["chat_error"]
        return "stubbed answer"

    monkeypatch.setattr(main, "rewrite_queries", fake_rewrite)
    monkeypatch.setattr(main, "embed", fake_embed)
    monkeypatch.setattr(main, "chat", fake_chat)
    monkeypatch.setattr(main, "_index", index)
    main._cv_sessions.clear()
    return calls


def _chat(payload):
    return TestClient(main.app).post("/api/chat", json=payload)


def test_chat_returns_content_and_sources(pipeline):
    resp = _chat({"message": "Can I ask for adjustments?", "history": []})

    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "stubbed answer"
    assert body["sources"][0]["source"] == "guides/adjustments.md"
    assert isinstance(body["sources"][0]["score"], float)


def test_chat_sends_system_prompt_cv_and_context_to_model(pipeline):
    main._cv_sessions["s1"] = {"filename": "cv.docx", "text": "Alex Example, Project Coordinator"}
    history = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]

    _chat({"message": "Improve my summary", "history": history, "session_id": "s1"})

    messages = pipeline["chat"][0]
    assert messages[0]["role"] == "system"
    assert "Evenbreak's UK-focused CV" in messages[0]["content"]
    assert messages[1] == main._cv_message(main._cv_sessions["s1"])
    assert ADJUSTMENTS in messages[2]["content"]
    assert messages[3:5] == history
    assert messages[-1] == {"role": "user", "content": "Improve my summary"}


def test_chat_embeds_every_rewritten_query(pipeline):
    pipeline["queries"] = ["adjustments at interview", "disclosure", "Access to Work"]

    _chat({"message": "What should I do?", "history": []})

    assert pipeline["embed"] == [["adjustments at interview", "disclosure", "Access to Work"]]


def test_chat_retrieves_at_most_k_passages(pipeline, monkeypatch):
    monkeypatch.setattr(main, "RETRIEVAL_K", 1)

    resp = _chat({"message": "Adjustments?", "history": []})

    assert len(resp.json()["sources"]) == 1


def test_chat_inference_error_returns_readable_error(pipeline):
    pipeline["chat_error"] = InferenceError("chat/completions returned 429: Too Many Requests")

    resp = _chat({"message": "Hello", "history": []})

    assert resp.status_code == 200
    assert resp.json()["content"].startswith("Error:")
    assert "429" in resp.json()["content"]
    assert resp.json()["sources"] == []


def test_chat_embedding_error_returns_readable_error(pipeline):
    pipeline["embed_error"] = InferenceError("embeddings returned 402: Payment Required")

    resp = _chat({"message": "Hello", "history": []})

    assert resp.status_code == 200
    assert resp.json()["content"].startswith("Error:")
    assert pipeline["chat"] == []


def test_startup_fails_with_helpful_message_when_index_missing(monkeypatch, tmp_path):
    missing = tmp_path / "nope.npz"
    monkeypatch.setenv("INDEX_PATH", str(missing))

    with pytest.raises(IndexLoadError, match=str(missing)):
        with TestClient(main.app):
            pass


def test_startup_loads_the_index(monkeypatch, tmp_path):
    path = tmp_path / "kb_index.npz"
    Index(
        [Chunk("a.md", "alpha")],
        np.array([[1.0, 0.0]], dtype=np.float32),
        embedding_model=main._config.embedding_model,
        source_hash="h",
        built_at="t",
    ).save(path)
    monkeypatch.setenv("INDEX_PATH", str(path))
    monkeypatch.setattr(main, "_index", None)

    with TestClient(main.app):
        assert main._index is not None
        assert len(main._index) == 1
