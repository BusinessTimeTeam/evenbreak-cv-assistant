"""Tests for the system prompt, query rewriting and message assembly."""

import asyncio

import pytest

import prompting
from inference import InferenceConfig, InferenceError
from prompting import MAX_QUERIES, build_messages, context_message, load_system_prompt, rewrite_queries
from retrieval import ScoredChunk

CONFIG = InferenceConfig(api_key="k")
MESSAGE = "How do I explain a gap in my CV?"


def _stub_chat(monkeypatch, reply=None, error=None):
    captured = {}

    async def fake_chat(config, messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        if error:
            raise error
        return reply

    monkeypatch.setattr(prompting, "chat", fake_chat)
    return captured


def _rewrite(history=None, cv_text=None):
    return asyncio.run(rewrite_queries(CONFIG, MESSAGE, history or [], cv_text))


def test_system_prompt_is_the_uk_cv_assistant_prompt():
    prompt = load_system_prompt()

    assert "You are Evenbreak's UK-focused CV, employment and disability-inclusion assistant." in prompt
    assert "Equality Act 2010" in prompt
    assert "Disability Discrimination Act 1995" in prompt


def test_rewrite_parses_json_array(monkeypatch):
    _stub_chat(monkeypatch, '["reasonable adjustments interview", "disclosing disability on CV"]')

    assert _rewrite() == ["reasonable adjustments interview", "disclosing disability on CV"]


def test_rewrite_extracts_array_from_fenced_or_prose_output(monkeypatch):
    _stub_chat(monkeypatch, 'Here you go:\n```json\n["a", "b"]\n```')

    assert _rewrite() == ["a", "b"]


@pytest.mark.parametrize("reply", ["not json", "[]", '{"q": "x"}', '["", "  "]', None])
def test_rewrite_falls_back_to_message_on_junk(monkeypatch, reply):
    _stub_chat(monkeypatch, reply)

    assert _rewrite() == [MESSAGE]


def test_rewrite_falls_back_to_message_on_inference_error(monkeypatch):
    _stub_chat(monkeypatch, error=InferenceError("402 Payment Required"))

    assert _rewrite() == [MESSAGE]


def test_rewrite_caps_query_count(monkeypatch):
    _stub_chat(monkeypatch, str([f"q{i}" for i in range(7)]).replace("'", '"'))

    assert _rewrite() == [f"q{i}" for i in range(MAX_QUERIES)]
    assert MAX_QUERIES == 4


def test_rewrite_prompt_includes_history_and_cv(monkeypatch):
    captured = _stub_chat(monkeypatch, '["q"]')
    history = [
        {"role": "user", "content": "I worked at Acme Widgets until 2021."},
        {"role": "assistant", "content": "Thanks, noted the Acme Widgets role."},
    ]

    _rewrite(history=history, cv_text="Alex Example — Project Coordinator")

    prompt = "\n".join(m["content"] for m in captured["messages"])
    assert "Thanks, noted the Acme Widgets role." in prompt
    assert "Alex Example — Project Coordinator" in prompt
    assert MESSAGE in prompt
    assert captured["kwargs"].get("temperature") == 0


def _passages():
    return [
        ScoredChunk("guides/adjustments.md", "Ask for adjustments early.", 0.9),
        ScoredChunk("course/gaps.md", "Be brief about gaps.", 0.7),
    ]


def test_build_messages_order():
    cv = {"role": "user", "content": "CV text"}
    history = [{"role": "user", "content": "h1"}, {"role": "assistant", "content": "h2"}]

    messages = build_messages("SYSTEM", cv, _passages(), history, MESSAGE)

    # Passages sit right before the latest message they were retrieved for, so
    # in a long conversation the model doesn't tie them to an older turn.
    assert [m["role"] for m in messages] == ["system", "user", "user", "assistant", "user", "user"]
    assert messages[0]["content"] == "SYSTEM"
    assert messages[1] is cv
    assert messages[2:4] == history
    assert "Ask for adjustments early." in messages[4]["content"]
    assert messages[-1] == {"role": "user", "content": MESSAGE}


def test_build_messages_without_cv_or_passages():
    history = [{"role": "user", "content": "h1"}]

    messages = build_messages("SYSTEM", None, [], history, MESSAGE)

    assert messages == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "h1"},
        {"role": "user", "content": MESSAGE},
    ]


def test_context_message_labels_each_passage_with_source():
    content = context_message(_passages())["content"]

    first = content.index("guides/adjustments.md")
    second = content.index("course/gaps.md")
    assert first < content.index("Ask for adjustments early.") < second
    assert "knowledge base" in content.lower()
