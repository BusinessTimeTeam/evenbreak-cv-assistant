"""Tests for the thinking-status summariser."""

import asyncio
import re

import status
import trial
from inference import InferenceConfig, InferenceError
from status import FALLBACK_PHRASES, StatusFeed, clean_status

CONFIG = InferenceConfig(api_key="k")
# Enough thinking for the first line to be summarised.
NOTES = "The user wants help explaining a gap in their CV to employers."
QUESTION = "How do I explain a gap in my CV?"


def _run_feed(summarise, adds=(), seconds=0.2, ledger=None):
    """Run a feed with a 0.02s interval for `seconds` and return what it emitted."""
    emitted = []

    async def go():
        feed = StatusFeed(CONFIG, emitted.append, interval=0.02, question=QUESTION, has_cv=False)
        for piece in adds:
            feed.add_thinking(piece)
        task = asyncio.create_task(feed.run())
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    if ledger is None:
        asyncio.run(go())
    else:
        with trial.recording(ledger):
            asyncio.run(go())
    return emitted


def test_clean_status_removes_links_urls_and_markup():
    assert clean_status("**Reading** [your CV](http://x.io) at https://evil.example now") == "Reading your CV at now"


def test_clean_status_strips_html_and_quotes_and_newlines():
    assert clean_status('"<b>Checking</b>\n your   dates"') == "Checking your dates"


def test_clean_status_truncates_at_word_boundary():
    out = clean_status("word " * 40)
    assert len(out) <= 80 and out.endswith("…") and not out.endswith(" …")


def test_clean_status_empty_when_nothing_left():
    assert clean_status("https://x.io **") == ""


def test_fallback_phrases_never_claim_progress():
    assert len(FALLBACK_PHRASES) >= 5
    assert all(not re.search(r"\b(nearly|almost|done|finish)", p, re.I) for p in FALLBACK_PHRASES)


def test_fallback_phrases_never_assume_something_was_shared():
    # They're shown whatever the turn, including a question with no CV.
    assert all(not re.search(r"\b(shared|cv|document|upload)", p, re.I) for p in FALLBACK_PHRASES)


def _summarise_prompt(monkeypatch, **context):
    calls = []

    async def fake_chat(config, messages, **kwargs):
        calls.append(messages)
        return "Working on it."

    monkeypatch.setattr(status, "chat", fake_chat)
    asyncio.run(status.summarise(CONFIG, "some thinking", "", **context))
    return calls[0][-1]["content"]


def test_summarise_gives_the_question_and_says_no_cv_is_attached(monkeypatch):
    prompt = _summarise_prompt(monkeypatch, question="How do I explain a gap?", has_cv=False)
    assert "How do I explain a gap?" in prompt
    assert "CV attached: no" in prompt


def test_summarise_says_when_a_cv_is_attached(monkeypatch):
    assert "CV attached: yes" in _summarise_prompt(monkeypatch, question="Improve my CV", has_cv=True)


def test_summarise_shortens_a_long_question(monkeypatch):
    prompt = _summarise_prompt(monkeypatch, question="word " * 1000, has_cv=False)
    assert prompt.count("word") <= status.QUESTION_MAX_CHARS // 5 + 1


def test_feed_gives_the_summariser_the_question_and_cv_flag(monkeypatch):
    seen = []

    async def fake_summarise(config, new_thinking, previous, **context):
        seen.append(context)
        return f"Line {len(seen)}"

    monkeypatch.setattr(status, "summarise", fake_summarise)
    _run_feed(fake_summarise, adds=(NOTES,))
    assert seen and seen[0] == {"question": QUESTION, "has_cv": False}


def test_summarise_uses_low_effort_on_the_fixed_model(monkeypatch):
    calls = []

    async def fake_chat(config, messages, **kwargs):
        calls.append({"messages": messages, "kwargs": kwargs})
        return "**Reframing** your skills."

    monkeypatch.setattr(status, "chat", fake_chat)
    out = asyncio.run(status.summarise(CONFIG, "some thinking", "Earlier line", question=QUESTION, has_cv=False))
    kwargs = calls[0]["kwargs"]
    assert out == "Reframing your skills."
    assert kwargs["model"] in (None, CONFIG.chat_model)
    assert kwargs["params"] == {"reasoning_effort": "low"}
    assert kwargs["max_tokens"] == 1024 and kwargs["temperature"] == 0.7
    prompt = " ".join(m["content"] for m in calls[0]["messages"])
    assert "<<<BEGIN CONTENT>>>\nsome thinking\n<<<END CONTENT>>>" in prompt
    assert "Earlier line" in prompt


def test_feed_emits_summaries_of_new_thinking(monkeypatch):
    seen = []

    async def fake_summarise(config, new_thinking, previous, **context):
        seen.append(new_thinking)
        return f"Line {len(seen)}"

    monkeypatch.setattr(status, "summarise", fake_summarise)
    emitted = _run_feed(fake_summarise, adds=(NOTES[:10], NOTES[10:]))
    assert emitted[:2] == ["Line 1", "Line 2"] and seen[0] == NOTES


def test_feed_first_line_is_a_fallback_when_there_is_little_thinking(monkeypatch):
    seen = []

    async def fake_summarise(config, new_thinking, previous, **context):
        seen.append(new_thinking)
        return f"Line {len(seen)}"

    monkeypatch.setattr(status, "summarise", fake_summarise)
    emitted = _run_feed(fake_summarise, adds=("The user",))
    assert emitted[0] in FALLBACK_PHRASES
    # Only the first cycle is skipped; later ones are summarised however short.
    assert seen[0] == "" and emitted[1] == "Line 1"


def test_feed_never_repeats_a_line(monkeypatch):
    async def fake_summarise(config, new_thinking, previous, **context):
        return "Same"

    monkeypatch.setattr(status, "summarise", fake_summarise)
    emitted = _run_feed(fake_summarise)
    assert len(emitted) >= 3
    assert all(a != b for a, b in zip(emitted, emitted[1:]))


def test_feed_falls_back_when_summary_fails(monkeypatch):
    async def fake_summarise(config, new_thinking, previous, **context):
        raise InferenceError("boom")

    monkeypatch.setattr(status, "summarise", fake_summarise)
    emitted = _run_feed(fake_summarise)
    assert emitted[0] in FALLBACK_PHRASES


def test_feed_falls_back_when_summary_is_slow(monkeypatch):
    async def fake_summarise(config, new_thinking, previous, **context):
        await asyncio.sleep(1)
        return "Too late"

    monkeypatch.setattr(status, "summarise", fake_summarise)
    emitted = _run_feed(fake_summarise)
    assert emitted[0] in FALLBACK_PHRASES


def test_feed_summary_cost_lands_in_the_ledger_as_status(monkeypatch):
    async def fake_summarise(config, new_thinking, previous, **context):
        trial.record_usage(CONFIG.chat_model, {"prompt_tokens": 450, "completion_tokens": 50})
        return "Reading your CV."

    monkeypatch.setattr(status, "summarise", fake_summarise)
    ledger = trial.Ledger()
    _run_feed(fake_summarise, adds=(NOTES,), seconds=0.01, ledger=ledger)
    assert ledger.tokens("status") == (450, 50)
