"""Tests for the comparison script. HTTP is stubbed."""

import asyncio
import json

import httpx

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


def test_run_new_uploads_cv_once_and_reuses_session(tmp_path):
    calls = {"upload": 0, "chat": []}

    def handler(request):
        if request.url.path == "/api/upload":
            calls["upload"] += 1
            return httpx.Response(200, json={"session_id": "s-1", "status": "ready"})
        body = json.loads(request.content)
        calls["chat"].append(body)
        return httpx.Response(
            200,
            json={"content": f"new {len(calls['chat'])}", "sources": [{"source": "a.md", "score": 0.9}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://app")
    result = asyncio.run(compare.run_new(CASE, _cvs(tmp_path), "http://app", client=client))

    assert calls["upload"] == 1
    assert [c["session_id"] for c in calls["chat"]] == ["s-1", "s-1"]
    assert calls["chat"][1]["history"] == [
        {"role": "user", "content": "First question?"},
        {"role": "assistant", "content": "new 1"},
    ]
    assert result[1] == {"turn": "Follow-up about that?", "answer": "new 2", "sources": ["a.md"]}


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
