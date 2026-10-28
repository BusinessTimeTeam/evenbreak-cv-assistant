"""Tests for the comparison script. HTTP is stubbed."""

import asyncio
import json

import httpx
import pytest

import compare

CASE = {
    "id": "two-turn",
    "cv": "cv.md",
    "turns": ["First question?", "Follow-up about that?"],
    "notes": "",
}


def _cvs(tmp_path):
    (tmp_path / "cv.md").write_text("Alex Example\nStore Manager")
    return tmp_path


def _ndjson(*events):
    body = "".join(json.dumps(e) + "\n" for e in events)
    return httpx.Response(200, headers={"content-type": "application/x-ndjson"}, content=body.encode())


def test_run_new_uploads_cv_once_and_reuses_session(tmp_path):
    calls = {"upload": 0, "chat": []}

    def handler(request):
        if request.url.path == "/api/upload":
            calls["upload"] += 1
            return httpx.Response(
                200,
                json={
                    "session_id": "s-1",
                    "status": "ready",
                    "history_token": "token-0",
                    "model": "m-1",
                    "total_microusd": 5,
                },
            )
        body = json.loads(request.content)
        calls["chat"].append(body)
        done = {
            "type": "done",
            "content": f"new {len(calls['chat'])}",
            "blocked": None,
            "sources": [{"source": "a.md", "score": 0.9}],
            "history": [{"role": "user", "content": "echo"}],
            "history_token": f"token-{len(calls['chat'])}",
            "session_id": "s-1",
            "model": "m-1",
            "total_microusd": 5 + 10 * len(calls["chat"]),
        }
        return _ndjson({"type": "answer", "text": "partial"}, done)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://app")
    result = asyncio.run(compare.run_new(CASE, _cvs(tmp_path), "http://app", client=client))

    assert calls["upload"] == 1
    assert [c["session_id"] for c in calls["chat"]] == ["s-1", "s-1"]
    # The upload's state starts the conversation; each reply's state continues it.
    assert calls["chat"][0]["history"] == [] and calls["chat"][0]["history_token"] == "token-0"
    assert calls["chat"][0]["model"] == "m-1" and calls["chat"][0]["total_microusd"] == 5
    assert calls["chat"][1]["history"] == [{"role": "user", "content": "echo"}]
    assert calls["chat"][1]["history_token"] == "token-1"
    assert calls["chat"][1]["model"] == "m-1" and calls["chat"][1]["total_microusd"] == 15
    assert result[1] == {"turn": "Follow-up about that?", "answer": "new 2", "sources": ["a.md"]}


def test_run_new_records_error_event(tmp_path):
    chat = []

    def handler(request):
        if request.url.path == "/api/upload":
            return httpx.Response(200, json={"session_id": "s-1", "status": "ready"})
        chat.append(json.loads(request.content))
        return _ndjson(
            {"type": "status", "text": "Thinking"},
            {
                "type": "error",
                "error": "boom",
                "history": [],
                "history_token": f"err-{len(chat)}",
                "session_id": "s-1",
                "model": "m-1",
                "total_microusd": 7,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://app")
    result = asyncio.run(compare.run_new(CASE, _cvs(tmp_path), "http://app", client=client))

    assert result[0] == {"turn": "First question?", "answer": "Error: boom", "sources": []}
    assert result[0]["answer"].startswith("Error: ")
    assert chat[1]["history_token"] == "err-1"
    assert chat[1]["total_microusd"] == 7


def test_run_new_qa_case_starts_without_state_then_sends_it_back(tmp_path):
    chat = []

    def handler(request):
        chat.append(json.loads(request.content))
        return _ndjson(
            {
                "type": "done",
                "content": "ok",
                "blocked": None,
                "sources": [],
                "history": [],
                "history_token": "t",
                "session_id": "qa-1",
                "model": "m-1",
                "total_microusd": 3,
            }
        )

    case = {**CASE, "cv": None}
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://app")
    asyncio.run(compare.run_new(case, tmp_path, "http://app", client=client))

    assert chat[0]["session_id"] is None and chat[0]["history_token"] is None
    assert chat[1] == {
        "message": "Follow-up about that?",
        "history": [],
        "history_token": "t",
        "session_id": "qa-1",
        "model": "m-1",
        "total_microusd": 3,
    }


def test_run_new_raises_clearly_when_the_stream_has_no_final_event(tmp_path):
    def handler(request):
        if request.url.path == "/api/upload":
            return httpx.Response(200, json={"session_id": "s-1", "status": "ready"})
        return _ndjson({"type": "answer", "text": "partial"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://app")
    with pytest.raises(RuntimeError, match="without a final event"):
        asyncio.run(compare.run_new(CASE, _cvs(tmp_path), "http://app", client=client))


def test_render_report_shows_both_answers_and_sources_per_turn():
    old = {"two-turn": [{"turn": "First question?", "answer": "OLD-1"}, {"turn": "Follow-up about that?", "answer": "OLD-2"}]}
    new = {
        "two-turn": [
            {"turn": "First question?", "answer": "NEW-1", "sources": ["guides/a.md", "course/b.md"]},
            {"turn": "Follow-up about that?", "answer": "NEW-2", "sources": []},
        ]
    }

    report = compare.render_report([CASE], old, new)

    assert "two-turn" in report
    assert "cv.md" in report
    for text in ("First question?", "OLD-1", "NEW-1", "Follow-up about that?", "OLD-2", "NEW-2"):
        assert text in report
    assert report.index("OLD-1") < report.index("NEW-1") < report.index("OLD-2")
    assert "guides/a.md" in report and "course/b.md" in report


def test_render_report_marks_missing_baseline():
    report = compare.render_report([CASE], {}, {"two-turn": [{"turn": "First question?", "answer": "NEW-1", "sources": []}]})

    assert "no baseline" in report.lower()
