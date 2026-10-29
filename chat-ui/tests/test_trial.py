"""Tests for the model-trial list, prices and cost ledger."""

import asyncio
import logging

import pytest

import trial


def _model(name):
    return next(m for m in trial.MODELS if m.name == name)


PRO = _model("DeepSeek V4 Pro")
GPT_OSS = _model("gpt-oss-120b")
EMBEDDING = "qwen3-embedding-0.6b"


def test_models_cover_the_spec_table():
    assert [m.name for m in trial.MODELS] == [
        "gpt-oss-120b", "DeepSeek V4 Flash", "Qwen 3.5 397B", "DeepSeek V4 Pro", "GLM-5.3", "Kimi K3"
    ]
    assert {m.group for m in trial.MODELS} <= set(trial.GROUPS)
    assert (PRO.group, PRO.input_price, PRO.output_price) == ("Premium · open", 1.74, 3.48)
    assert (GPT_OSS.group, GPT_OSS.input_price, GPT_OSS.output_price) == ("Budget · open", 0.10, 0.70)


def test_every_model_and_the_embedding_model_is_priced():
    for m in trial.MODELS:
        assert trial.PRICES[m.id] == (m.input_price, m.output_price)
    assert trial.PRICES[EMBEDDING] == (0.04, 0.0)


def test_find_model():
    assert trial.find_model(PRO.id) is PRO
    assert trial.find_model("no-such-model") is None


def test_cost_is_micro_usd_from_per_million_prices():
    ledger = trial.Ledger()
    ledger.record("reply", PRO.id, 1_000, 500)  # 1000*1.74 + 500*3.48 = 3480
    ledger.record("embed", EMBEDDING, 30, 0)  # 30*0.04 = 1.2
    assert ledger.microusd() == 3481


def test_record_usage_without_a_ledger_is_a_no_op():
    trial.record_usage(GPT_OSS.id, {"prompt_tokens": 10, "completion_tokens": 2})


def test_record_usage_uses_current_purpose():
    ledger = trial.Ledger()
    with trial.recording(ledger), trial.purpose("guard"):
        trial.record_usage(GPT_OSS.id, {"prompt_tokens": 10, "completion_tokens": 2})
    assert ledger.tokens("guard") == (10, 2)
    assert ledger.tokens("reply") == (0, 0)


def test_record_usage_defaults_to_other_purpose():
    ledger = trial.Ledger()
    with trial.recording(ledger):
        trial.record_usage(GPT_OSS.id, {"prompt_tokens": 4})
    assert ledger.tokens("other") == (4, 0)


def test_missing_usage_counts_zero_and_warns(caplog):
    ledger = trial.Ledger()
    with caplog.at_level(logging.WARNING), trial.recording(ledger):
        trial.record_usage(GPT_OSS.id, None)
    assert ledger.microusd() == 0
    assert GPT_OSS.id in caplog.text


def test_unpriced_model_counts_zero_and_warns(caplog):
    ledger = trial.Ledger()
    with caplog.at_level(logging.WARNING), trial.recording(ledger):
        trial.record_usage("mystery-model", {"prompt_tokens": 100, "completion_tokens": 100})
    assert ledger.microusd() == 0
    assert "mystery-model" in caplog.text


def test_usage_from_gathered_tasks_lands_in_one_ledger():
    ledger = trial.Ledger()

    async def call(name):
        with trial.purpose(name):
            await asyncio.sleep(0)
            trial.record_usage(GPT_OSS.id, {"prompt_tokens": 1, "completion_tokens": 1})

    async def run():
        with trial.recording(ledger):
            await asyncio.gather(call("guard"), call("rewrite"))

    asyncio.run(run())
    assert ledger.tokens("guard") == (1, 1)
    assert ledger.tokens("rewrite") == (1, 1)


def test_cost_log_line_has_fields_and_no_text():
    ledger = trial.Ledger()
    ledger.record("reply", PRO.id, 1_000, 500)
    line = trial.cost_log_line("chat", "answered", "qa-1", PRO.id, ledger, 12345)
    for field in (
        "kind=chat",
        "outcome=answered",
        "session=qa-1",
        f"model={PRO.id}",
        "reply_in=1000",
        "reply_out=500",
        "guard_in=0",
        "guard_out=0",
        "rewrite_in=0",
        "rewrite_out=0",
        "status_in=0",
        "status_out=0",
        "embed_in=0",
        "turn_usd=0.003480",
        "total_usd=0.012345",
    ):
        assert field in line
    assert line.index("rewrite_out=") < line.index("status_in=") < line.index("status_out=") < line.index("embed_in=")


def test_kimi_k3_sends_the_top_p_it_requires():
    assert _model("Kimi K3").params == {"top_p": 0.95}


def test_models_payload_groups_in_order_with_default():
    payload = trial.models_payload(GPT_OSS.id)
    assert payload["default"] == GPT_OSS.id
    # Groups keep GROUPS order; groups with no models are left out.
    assert [g["label"] for g in payload["groups"]] == [g for g in trial.GROUPS if "open" in g]
    first = payload["groups"][0]["models"][0]
    assert first == {"id": GPT_OSS.id, "name": "gpt-oss-120b"}
    assert sum(len(g["models"]) for g in payload["groups"]) == len(trial.MODELS)


@pytest.mark.parametrize("usage", ["x", ["prompt_tokens"], {"prompt_tokens": "n/a", "completion_tokens": {}}])
def test_malformed_usage_counts_zero_and_warns(caplog, usage):
    ledger = trial.Ledger()
    with caplog.at_level(logging.WARNING), trial.recording(ledger):
        trial.record_usage(GPT_OSS.id, usage)
    assert ledger.microusd() == 0
    assert GPT_OSS.id in caplog.text
