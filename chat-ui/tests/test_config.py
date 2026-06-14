import pytest

from main import _require_config


def test_both_present_does_not_raise():
    # Both required vars set -> no error.
    assert (
        _require_config(
            endpoint="https://x.agents.do-ai.run/api/v1/chat/completions",
            api_key="sk-local",
        )
        is None
    )


def test_missing_api_key_raises_naming_it():
    with pytest.raises(RuntimeError) as exc:
        _require_config(
            endpoint="https://x.agents.do-ai.run/api/v1/chat/completions",
            api_key=None,
        )
    assert "AGENT_API_KEY" in str(exc.value)


def test_missing_endpoint_raises_naming_it():
    with pytest.raises(RuntimeError) as exc:
        _require_config(endpoint=None, api_key="sk-local")
    assert "AGENT_ENDPOINT" in str(exc.value)


def test_missing_both_raises_naming_both():
    with pytest.raises(RuntimeError) as exc:
        _require_config(endpoint=None, api_key=None)
    msg = str(exc.value)
    assert "AGENT_ENDPOINT" in msg
    assert "AGENT_API_KEY" in msg
