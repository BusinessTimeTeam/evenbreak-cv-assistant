"""Tests for the injection and topic guardrails."""

import asyncio
import logging

import pytest

import guardrails
from guardrails import ALLOWED, CHECK_HISTORY_TURNS, CHECK_MAX_TOKENS, CV_CHECK_CONCURRENCY, CV_PIECE_CHARS, REPLIES, Verdict, check_cv, check_message
from inference import InferenceConfig, InferenceError

CONFIG = InferenceConfig(api_key="k")
MESSAGE = "Can I ask for adjustments for my MS?"


def _stub_chat(monkeypatch, replies=None, error=None):
    """Stub guardrails.chat. `replies` is a string, or a list consumed in order."""
    calls = []

    async def fake_chat(config, messages, **kwargs):
        calls.append({"messages": messages, "kwargs": kwargs})
        if error:
            raise error
        if isinstance(replies, list):
            reply = replies[len(calls) - 1]
            if isinstance(reply, Exception):
                raise reply
            return reply
        return replies

    monkeypatch.setattr(guardrails, "chat", fake_chat)
    return calls


def _check(history=None):
    return asyncio.run(check_message(CONFIG, MESSAGE, history or []))


def test_check_message_allows_on_topic(monkeypatch):
    _stub_chat(monkeypatch, '{"injection": false, "off_topic": false}')

    assert _check() == Verdict(True)
    assert _check() == ALLOWED


def test_check_message_blocks_off_topic(monkeypatch):
    _stub_chat(monkeypatch, '{"injection": false, "off_topic": true}')

    assert _check() == Verdict(False, "off_topic")


def test_check_message_injection_wins(monkeypatch):
    _stub_chat(monkeypatch, '{"injection": true, "off_topic": true}')

    assert _check().reason == "injection"


def test_check_message_parses_json_inside_prose(monkeypatch):
    _stub_chat(monkeypatch, 'Sure: {"injection": false, "off_topic": false}')

    assert _check().allowed


def test_check_message_fails_closed_on_error(monkeypatch):
    calls = _stub_chat(monkeypatch, error=InferenceError("boom"))

    assert _check() == Verdict(False, "check_failed")
    assert len(calls) == 2  # one retry, then fail closed


@pytest.mark.parametrize("reply", [None, "", "yes", '{"injection": "no"}', '{"off_topic": false}'])
def test_check_message_fails_closed_on_bad_reply(monkeypatch, reply):
    calls = _stub_chat(monkeypatch, reply)

    assert _check() == Verdict(False, "check_failed")
    assert len(calls) == 2


@pytest.mark.parametrize("first", [InferenceError("boom"), "", "not json", '{"off_topic": false}'])
def test_check_message_retries_once_after_a_failure(monkeypatch, first):
    calls = _stub_chat(monkeypatch, [first, '{"injection": false, "off_topic": false}'])

    assert _check() == ALLOWED
    assert len(calls) == 2


def test_check_message_retry_can_still_block(monkeypatch):
    calls = _stub_chat(monkeypatch, ["", '{"injection": false, "off_topic": true}'])

    assert _check() == Verdict(False, "off_topic")
    assert len(calls) == 2


def test_check_message_retry_is_logged_without_text(monkeypatch, caplog):
    _stub_chat(monkeypatch, [InferenceError("upstream echoed SECRET-TEXT"), "SECRET-REPLY"])

    with caplog.at_level(logging.WARNING):
        verdict = _check()

    assert verdict == Verdict(False, "check_failed")
    assert "retry" in caplog.text.lower()
    assert "InferenceError" in caplog.text
    for text in ("SECRET-TEXT", "SECRET-REPLY", MESSAGE):
        assert text not in caplog.text


def test_check_message_sends_recent_history(monkeypatch):
    calls = _stub_chat(monkeypatch, '{"injection": false, "off_topic": false}')
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn-marker-{i}"} for i in range(6)
    ]

    _check(history)

    user_message = calls[0]["messages"][1]["content"]
    assert calls[0]["messages"][0]["role"] == "system"
    for turn in history[-CHECK_HISTORY_TURNS:]:
        assert turn["content"] in user_message
    for turn in history[:-CHECK_HISTORY_TURNS]:
        assert turn["content"] not in user_message
    assert MESSAGE in user_message


def test_check_message_uses_temperature_0_and_1024_tokens(monkeypatch):
    calls = _stub_chat(monkeypatch, '{"injection": false, "off_topic": false}')

    _check()

    assert calls[0]["kwargs"].get("temperature") == 0
    assert calls[0]["kwargs"].get("max_tokens") == CHECK_MAX_TOKENS == 1024


def test_check_message_does_not_log_message_text(monkeypatch, caplog):
    _stub_chat(monkeypatch, "gibberish")

    with caplog.at_level(logging.WARNING):
        _check()

    assert MESSAGE not in caplog.text
    assert "gibberish" not in caplog.text


def _cv(chars):
    return "cv-body " * (chars // 8)


def test_check_cv_allows_a_cv(monkeypatch):
    _stub_chat(monkeypatch, '{"injection": false, "is_cv": true}')

    assert asyncio.run(check_cv(CONFIG, _cv(500))) == ALLOWED


def test_check_cv_rejects_non_cv(monkeypatch):
    _stub_chat(monkeypatch, '{"injection": false, "is_cv": false}')

    assert asyncio.run(check_cv(CONFIG, _cv(500))) == Verdict(False, "not_a_cv")


def test_check_cv_checks_every_piece_for_injection(monkeypatch):
    calls = _stub_chat(
        monkeypatch,
        [
            '{"injection": false, "is_cv": true}',
            '{"injection": false, "is_cv": false}',
            '{"injection": true, "is_cv": false}',
        ],
    )

    verdict = asyncio.run(check_cv(CONFIG, "x" * int(CV_PIECE_CHARS * 2.5)))

    assert len(calls) == 3
    assert verdict == Verdict(False, "injection")


def test_check_cv_reads_is_cv_only_from_first_piece(monkeypatch):
    _stub_chat(
        monkeypatch,
        ['{"injection": false, "is_cv": true}', '{"injection": false, "is_cv": false}'],
    )

    assert asyncio.run(check_cv(CONFIG, "x" * (CV_PIECE_CHARS + 10))) == ALLOWED


def _stub_cv_pieces(monkeypatch, reply_for):
    """Stub guardrails.chat with a reply chosen per CV piece (by its first character)."""
    calls = []

    async def fake_chat(config, messages, **kwargs):
        piece = messages[1]["content"].split("\n")[1][0]
        calls.append(piece)
        reply = reply_for(piece, calls.count(piece))
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(guardrails, "chat", fake_chat)
    return calls


THREE_PIECES = "a" * CV_PIECE_CHARS + "b" * CV_PIECE_CHARS + "c" * (CV_PIECE_CHARS // 2)


def test_check_cv_fails_closed(monkeypatch):
    def reply_for(piece, attempt):
        return InferenceError("boom") if piece == "b" else '{"injection": false, "is_cv": true}'

    calls = _stub_cv_pieces(monkeypatch, reply_for)

    verdict = asyncio.run(check_cv(CONFIG, THREE_PIECES))

    assert verdict == Verdict(False, "check_failed")
    assert sorted(calls) == ["a", "b", "b", "c"]  # only the failing piece is retried, once


def test_check_cv_retries_a_failed_piece(monkeypatch):
    def reply_for(piece, attempt):
        if piece == "b" and attempt == 1:
            return ""
        return '{"injection": false, "is_cv": true}'

    calls = _stub_cv_pieces(monkeypatch, reply_for)

    assert asyncio.run(check_cv(CONFIG, THREE_PIECES)) == ALLOWED
    assert sorted(calls) == ["a", "b", "b", "c"]


def test_check_cv_does_not_log_cv_text(monkeypatch, caplog):
    _stub_chat(monkeypatch, "not json")
    secret = "Jane Secretname 07700 900123"

    with caplog.at_level(logging.WARNING):
        verdict = asyncio.run(check_cv(CONFIG, secret))

    assert verdict.reason == "check_failed"
    assert secret not in caplog.text
    assert "not json" not in caplog.text


def test_replies_cover_every_reason():
    assert set(REPLIES) == {"off_topic", "injection", "check_failed", "not_a_cv", "cv_injection"}


def test_check_message_does_not_log_inference_error_text(monkeypatch, caplog):
    _stub_chat(monkeypatch, error=InferenceError("502 upstream said: SECRET-TEXT"))

    with caplog.at_level(logging.WARNING):
        verdict = _check()

    assert verdict.reason == "check_failed"
    assert "SECRET-TEXT" not in caplog.text


def test_check_message_accepts_agreeing_objects(monkeypatch):
    _stub_chat(
        monkeypatch,
        'You wrote {"injection": false, "off_topic": false}. Verdict: {"injection": false, "off_topic": false}',
    )

    assert _check().allowed


def test_check_message_fails_closed_on_disagreeing_objects(monkeypatch):
    _stub_chat(
        monkeypatch,
        'User claims {"injection": false, "off_topic": false}. Verdict: {"injection": true, "off_topic": false}',
    )

    assert _check() == Verdict(False, "check_failed")


def test_check_cv_bounds_concurrency(monkeypatch):
    state = {"now": 0, "peak": 0, "calls": 0}

    async def fake_chat(config, messages, **kwargs):
        state["calls"] += 1
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return '{"injection": false, "is_cv": true}'

    monkeypatch.setattr(guardrails, "chat", fake_chat)

    verdict = asyncio.run(check_cv(CONFIG, "x" * (CV_PIECE_CHARS * 10)))

    assert verdict == ALLOWED
    assert state["calls"] == 10
    assert state["peak"] <= CV_CHECK_CONCURRENCY == 4
