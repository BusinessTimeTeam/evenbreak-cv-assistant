"""Model trial: the models users can pick from, their prices, and cost tracking.

TEMPORARY. A select group of users picks the chat model for each conversation
so the team can compare quality and cost per conversation, then settle on one.
To remove the trial: delete this module, the "Model trial" blocks in
static/index.html and static/styles.css, and the ``trial`` calls in main.py and
inference.py; then set CHAT_MODEL to the chosen model.

Costs are integer micro-USD. Prices are USD per million tokens, which is also
micro-USD per token, so a call costs ``in_tokens * in_price + out_tokens *
out_price`` micro-USD. Prices are from DigitalOcean's pricing page, checked
2026-10-28 (the lower prompt-size band where a model has two).

inference.py reports every response's ``usage`` through ``record_usage``, which
adds it to the Ledger made current by ``recording`` (a ContextVar, so it also
reaches calls made inside asyncio.gather tasks), labelled with the current
``purpose``.
"""

import logging
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrialModel:
    id: str
    name: str
    group: str
    input_price: float
    output_price: float
    # Per-model request overrides passed to inference.chat (None removes a key).
    params: dict = field(default_factory=dict)


GROUPS = ["Budget · open", "Budget · closed", "Mid-range · open", "Premium · open", "Premium · closed"]

# The closed models return 403 "not available for your subscription tier" on
# the DigitalOcean account (checked 2026-10-28 with check_models.py). Uncomment
# them once the account can use them; their groups reappear in the picker.
MODELS = [
    TrialModel("openai-gpt-oss-120b", "gpt-oss-120b", "Budget · open", 0.10, 0.70),
    TrialModel("deepseek-4-flash", "DeepSeek V4 Flash", "Budget · open", 0.14, 0.28),
    # TrialModel("anthropic-claude-5.5-haiku", "Claude Haiku 5.5", "Budget · closed", 0.10, 0.50),
    # TrialModel("openai-gpt-6-luna", "GPT-6 Luna", "Budget · closed", 0.10, 0.50),
    TrialModel("qwen3.5-397b-a17b", "Qwen 3.5 397B", "Mid-range · open", 0.55, 3.50),
    TrialModel("deepseek-v4-pro", "DeepSeek V4 Pro", "Premium · open", 1.74, 3.48),
    TrialModel("glm-5.3", "GLM-5.3", "Premium · open", 1.40, 4.40),
    TrialModel("kimi-k3", "Kimi K3", "Premium · open", 3.00, 15.00, {"top_p": 0.95}),
    # TrialModel("anthropic-claude-sonnet-5.5", "Claude Sonnet 5.5", "Premium · closed", 2.00, 10.00),
    # TrialModel("openai-gpt-6-sol", "GPT-6 Sol", "Premium · closed", 2.00, 10.00),
]

PRICES: dict[str, tuple[float, float]] = {
    **{m.id: (m.input_price, m.output_price) for m in MODELS},
    "qwen3-embedding-0.6b": (0.04, 0.0),
}

_ledger: ContextVar["Ledger | None"] = ContextVar("trial_ledger", default=None)
_purpose: ContextVar[str] = ContextVar("trial_purpose", default="other")


def find_model(model_id: str) -> TrialModel | None:
    return next((m for m in MODELS if m.id == model_id), None)


class Ledger:
    """Token usage and cost of the model calls made for one request."""

    def __init__(self) -> None:
        self._tokens: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        self._microusd = 0.0

    def record(self, purpose: str, model: str, input_tokens: int, output_tokens: int) -> None:
        price = PRICES.get(model)
        if price is None:
            logger.warning("No trial price for model %s; counting its cost as zero", model)
            price = (0.0, 0.0)
        totals = self._tokens[purpose]
        totals[0] += input_tokens
        totals[1] += output_tokens
        self._microusd += input_tokens * price[0] + output_tokens * price[1]

    def microusd(self) -> int:
        return round(self._microusd)

    def tokens(self, purpose: str) -> tuple[int, int]:
        totals = self._tokens.get(purpose, [0, 0])
        return totals[0], totals[1]


@contextmanager
def recording(ledger: Ledger):
    token = _ledger.set(ledger)
    try:
        yield ledger
    finally:
        _ledger.reset(token)


@contextmanager
def purpose(name: str):
    token = _purpose.set(name)
    try:
        yield
    finally:
        _purpose.reset(token)


def record_usage(model: str, usage: dict | None) -> None:
    ledger = _ledger.get()
    if ledger is None:
        return
    # Costing must never fail a turn, whatever shape a provider reports usage in.
    if not isinstance(usage, dict):
        logger.warning("Response from %s had no usable usage; counting its cost as zero", model)
        usage = {}
    try:
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        logger.warning("Response from %s had unreadable token counts; counting its cost as zero", model)
        input_tokens = output_tokens = 0
    ledger.record(_purpose.get(), model, input_tokens, output_tokens)


def cost_log_line(
    kind: str, outcome: str, session_id: str | None, model: str, ledger: Ledger, total_microusd: int
) -> str:
    reply, guard, rewrite, status, embed = (ledger.tokens(p) for p in ("reply", "guard", "rewrite", "status", "embed"))
    return (
        f"Turn cost: kind={kind} outcome={outcome} session={session_id} model={model} "
        f"reply_in={reply[0]} reply_out={reply[1]} guard_in={guard[0]} guard_out={guard[1]} "
        f"rewrite_in={rewrite[0]} rewrite_out={rewrite[1]} "
        f"status_in={status[0]} status_out={status[1]} embed_in={embed[0]} "
        f"turn_usd={ledger.microusd() / 1e6:.6f} total_usd={total_microusd / 1e6:.6f}"
    )


def models_payload(default: str) -> dict:
    groups = [
        {"label": g, "models": [{"id": m.id, "name": m.name} for m in MODELS if m.group == g]}
        for g in GROUPS
    ]
    return {"default": default, "groups": [g for g in groups if g["models"]]}
