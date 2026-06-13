import pytest

from main import _resolve_config


def test_overrides_present_skips_discovery():
    # Both override vars set -> no discovery needed, no DO creds required.
    assert _resolve_config(
        endpoint="https://x.agents.do-ai.run/api/v1/chat/completions",
        api_key="sk-local",
        agent_uuid=None,
        do_token=None,
    ) is False


def test_discovery_path_when_creds_present():
    # No overrides, but discovery creds present -> discovery needed.
    assert _resolve_config(
        endpoint=None,
        api_key=None,
        agent_uuid="agent-123",
        do_token="dop_v1_x",
    ) is True


def test_missing_everything_raises_naming_vars():
    with pytest.raises(RuntimeError) as exc:
        _resolve_config(endpoint=None, api_key=None, agent_uuid=None, do_token=None)
    msg = str(exc.value)
    assert "AGENT_UUID" in msg
    assert "DO_API_TOKEN" in msg


def test_partial_override_falls_back_to_discovery_and_validates():
    # Only endpoint set (no api key) and no discovery creds -> error naming the
    # missing discovery vars.
    with pytest.raises(RuntimeError) as exc:
        _resolve_config(
            endpoint="https://x.agents.do-ai.run/api/v1/chat/completions",
            api_key=None,
            agent_uuid=None,
            do_token=None,
        )
    assert "DO_API_TOKEN" in str(exc.value)
