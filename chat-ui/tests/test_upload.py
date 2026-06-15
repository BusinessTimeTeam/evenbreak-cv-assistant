"""Tests for the /api/upload endpoint and CV-aware /api/chat.

Uploaded CVs are stored server-side keyed by a session id and injected as a
stable leading message on each agent call. The agent call is stubbed so these
tests stay offline.
"""

import io
import os

import pytest
from docx import Document

# main reads these at import time; set before importing it.
os.environ.setdefault("AGENT_ENDPOINT", "https://agent.example/api/v1/chat/completions")
os.environ.setdefault("AGENT_API_KEY", "sk-test")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    captured = {}

    async def fake_call_agent(messages):
        captured["messages"] = messages
        return "stubbed agent reply"

    monkeypatch.setattr(main, "_call_agent", fake_call_agent)
    # Start each test with an empty session store.
    main._cv_sessions.clear()
    test_client = TestClient(main.app)
    test_client.captured = captured
    return test_client


def _docx_bytes(*paragraphs: str) -> bytes:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _upload(client, *paragraphs, filename="cv.docx", message=None):
    data = _docx_bytes(*paragraphs)
    form = {"message": message} if message is not None else None
    return client.post(
        "/api/upload",
        files={"file": (filename, data, "application/octet-stream")},
        data=form,
    )


def test_cv_message_uses_user_role_not_system():
    # The DO managed agent rejects client-supplied system/developer messages
    # ("agent instructions are set via agent configuration", HTTP 400), so the
    # CV must ride along as a user message.
    msg = main._cv_message({"filename": "cv.pdf", "text": "Ada Lovelace"})
    assert msg["role"] == "user"
    assert "Ada Lovelace" in msg["content"]


def test_upload_extracts_and_forwards_cv_to_agent(client):
    resp = _upload(client, "Ada Lovelace", "Mathematician")
    assert resp.status_code == 200
    assert resp.json()["content"] == "stubbed agent reply"

    # The CV text reached the agent somewhere in the messages.
    messages = client.captured["messages"]
    assert any("Ada Lovelace" in m["content"] for m in messages)
    assert any("Mathematician" in m["content"] for m in messages)


def test_upload_returns_a_session_id(client):
    resp = _upload(client, "Ada Lovelace")
    assert resp.json().get("session_id")


def test_upload_includes_user_message_when_provided(client):
    resp = _upload(
        client,
        "Grace Hopper",
        message="Is this candidate a good fit for a Python role?",
    )
    assert resp.status_code == 200
    messages = client.captured["messages"]
    # The instruction is the final user turn; the CV is carried separately.
    assert "Python role" in messages[-1]["content"]
    assert any("Grace Hopper" in m["content"] for m in messages)


def test_upload_rejects_unsupported_file_type(client):
    resp = client.post(
        "/api/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400
    assert "Unsupported" in resp.json()["error"]


def test_chat_injects_stored_cv_as_leading_message(client):
    session_id = _upload(client, "Marie Curie", "Physicist").json()["session_id"]

    resp = client.post(
        "/api/chat",
        json={"message": "What field are they in?", "session_id": session_id, "history": []},
    )
    assert resp.status_code == 200

    messages = client.captured["messages"]
    # CV is the stable leading message; the live question is last.
    assert "Marie Curie" in messages[0]["content"]
    assert messages[-1]["content"] == "What field are they in?"


def test_chat_without_session_sends_no_cv_prefix(client):
    resp = client.post("/api/chat", json={"message": "hello", "history": []})
    assert resp.status_code == 200
    assert client.captured["messages"] == [{"role": "user", "content": "hello"}]


def test_chat_with_unknown_session_sends_no_cv_prefix(client):
    resp = client.post(
        "/api/chat",
        json={"message": "hello", "session_id": "does-not-exist", "history": []},
    )
    assert resp.status_code == 200
    assert client.captured["messages"] == [{"role": "user", "content": "hello"}]
