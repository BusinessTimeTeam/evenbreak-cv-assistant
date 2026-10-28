"""Check the model trial's models against DigitalOcean serverless inference.

Manual, not a test: it calls the live API with MODEL_ACCESS_KEY (costs a
fraction of a cent). Lists the models the key can see, reports trial models
that aren't among them (with close matches), then sends each trial model one
tiny chat with the request settings the app uses for answers and prints OK,
EMPTY or ERROR with the usage returned.

    docker compose --profile tools run --rm evals python /app/check_models.py
"""

import asyncio
import re
import sys

import httpx

import trial
from inference import InferenceConfig, InferenceError, chat

PROMPT = [{"role": "user", "content": "Reply with the word OK."}]


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9.]+", text.lower())) - {"openai", "anthropic", "claude"}


async def _available(config: InferenceConfig, client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get(
        f"{config.base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {config.api_key}"},
    )
    resp.raise_for_status()
    return resp.json()["data"]


async def _try(config: InferenceConfig, model: trial.TrialModel, client: httpx.AsyncClient) -> str:
    seen = {}

    def capture(model_id, usage):
        seen["usage"] = usage

    original = trial.record_usage
    trial.record_usage = capture
    try:
        reply = await chat(config, PROMPT, model=model.id, params=model.params, client=client)
    except InferenceError as exc:
        return f"ERROR {str(exc)[:300]}"
    finally:
        trial.record_usage = original
    status = "OK" if reply.strip() else "EMPTY"
    return f"{status} reply={reply.strip()[:40]!r} usage={seen.get('usage')}"


async def main() -> int:
    config = InferenceConfig.from_env()
    async with httpx.AsyncClient(timeout=120) as client:
        available = await _available(config, client)
        ids = sorted(m["id"] for m in available)
        print(f"{len(ids)} models visible to this key")
        for m in trial.MODELS:
            if m.id in ids:
                continue
            close = [i for i in ids if _words(m.name) & _words(i)]
            print(f"MISSING {m.id} ({m.name}); close matches: {close}")
        kimi = [m for m in available if "kimi" in m["id"].lower()]
        if kimi:
            print(f"Kimi entries: {kimi}")
        print()
        failures = 0
        for m in trial.MODELS:
            result = await _try(config, m, client)
            failures += not result.startswith("OK")
            print(f"{m.name:20} {m.id:32} {result}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
