# Model Choice and Conversation Cost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let trial users pick one of ten chat models per conversation, lock it once the conversation starts, and show and log the conversation's full running cost.

**Architecture:** A new, removable `chat-ui/trial.py` holds the model list, prices and a per-request cost `Ledger`. `inference.py` reports each response's `usage` into whichever ledger is active, found through a `ContextVar`, so the guardrails and prompting code don't change. The model and running total (integer micro-dollars) are signed into the existing history token. The CV session also stores its model. The page gets a picker on the landing page and a status strip during the conversation.

**Tech Stack:** Python 3 / FastAPI / httpx / pytest; vanilla JS in `chat-ui/static/index.html`; Terraform variable text.

**Spec:** `docs/superpowers/specs/2026-10-28-model-choice-and-conversation-cost-design.md`

## Global Constraints

- Only the reply uses the chosen model. Guardrails and query rewriting use `config.chat_model` (gpt-oss-20b); embeddings use `config.embedding_model`.
- Cost counts every call: the CV check, the message check, rewriting, embedding, the reply, and retries.
- Money is integer **micro-USD** (`microusd`) everywhere it is stored, signed or sent. A price in USD per 1M tokens is also µUSD per token, so `cost = in_tokens * in_price + out_tokens * out_price`.
- Display: US dollars to 4 decimal places, e.g. `Conversation cost so far: $0.0412`.
- Never log message text, CV text or the CV filename.
- An unknown model id from the client → HTTP 400 `{"error": "Unknown model."}`.
- A missing `usage` in a response → `logger.warning` and count zero; never fail the turn.
- Every trial-only piece lives in `trial.py`, plus one marked block each in `index.html` and `styles.css`, so it can be removed.

## Review Focus

- Usage recorded from calls inside `asyncio.gather` tasks (the check and retrieval run concurrently) must land in the same ledger → test in Task 1.
- A blocked or failed *first* Q&A turn must still return a session id, model, total and token, so the next turn continues the same conversation → test in Task 4.
- A client that edits `total_microusd` or `model` (including sending a float or a string) is rejected as expired, not crashed → tests in Tasks 3 and 4.
- A response with no `usage` block is counted as zero with a warning → test in Task 2.
- An upload naming an unknown model is rejected before the CV check runs, so it costs nothing → test in Task 4.

---

### Task 1: `trial.py` — models, prices and the cost ledger

**Files:**
- Create: `chat-ui/trial.py`
- Test: `chat-ui/tests/test_trial.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class TrialModel: id: str; name: str; group: str; input_price: float; output_price: float; params: dict` (`params` holds per-model request overrides, default `{}`)
  - `GROUPS: list[str]` = `["Budget · open", "Budget · closed", "Mid-range · open", "Premium · open", "Premium · closed"]`
  - `MODELS: list[TrialModel]` in the order of the spec's table, grouped as in the spec
  - `PRICES: dict[str, tuple[float, float]]` = every `MODELS` entry plus the embedding model (`0.04, 0.0`)
  - `def find_model(model_id: str) -> TrialModel | None`
  - `class Ledger` with `record(purpose: str, model: str, input_tokens: int, output_tokens: int) -> None`, `microusd() -> int` (rounded sum), `tokens(purpose: str) -> tuple[int, int]`
  - `@contextmanager def recording(ledger: Ledger)` and `@contextmanager def purpose(name: str)`, each setting a module `ContextVar` and resetting it on exit
  - `def record_usage(model: str, usage: dict | None) -> None`: no-op when no ledger is active. Otherwise it reads `prompt_tokens` and `completion_tokens` (default 0). If `usage` is None it logs a warning naming only the model and records zeros. It records under the current purpose (default `"other"`). An unpriced model is priced at 0 with a warning.
  - `def cost_log_line(kind: str, outcome: str, session_id: str | None, model: str, ledger: Ledger, total_microusd: int) -> str`
  - `def models_payload(default: str) -> dict` → `{"default": default, "groups": [{"label": g, "models": [{"id", "name"}]}]}`, groups in `GROUPS` order, empty groups omitted
- Purposes used later: `"guard"`, `"rewrite"`, `"embed"`, `"reply"`.

Provisional IDs. Task 5 replaces them with the real IDs from `/models`:
`openai-gpt-oss-20b`, `deepseek-v4-flash`, `anthropic-claude-haiku-5.5`, `openai-gpt-6-luna`, `qwen3.5-397b-a17b`, `deepseek-v4-pro`, `glm-5.3`, `kimi-k3`, `anthropic-claude-sonnet-5.5`, `openai-gpt-6-sol`. Names and prices come exactly from the spec's table (lower price band only).

- [ ] **Step 1: Write the failing tests** in `chat-ui/tests/test_trial.py`:

```python
def test_models_cover_the_spec_table():
    assert len(trial.MODELS) == 10
    assert {m.group for m in trial.MODELS} == set(trial.GROUPS)
    sonnet = next(m for m in trial.MODELS if m.name == "Claude Sonnet 5.5")
    assert (sonnet.group, sonnet.input_price, sonnet.output_price) == ("Premium · closed", 2.00, 10.00)

def test_cost_is_micro_usd_from_per_million_prices():
    ledger = trial.Ledger()
    ledger.record("reply", sonnet_id, 1_000, 500)       # 1000*2 + 500*10
    ledger.record("embed", "qwen3-embedding-0.6b", 30, 0)  # 30*0.04 = 1.2
    assert ledger.microusd() == 7001

def test_record_usage_without_a_ledger_is_a_no_op(): ...
def test_record_usage_uses_current_purpose():
    with trial.recording(ledger), trial.purpose("guard"):
        trial.record_usage(gpt_oss_id, {"prompt_tokens": 10, "completion_tokens": 2})
    assert ledger.tokens("guard") == (10, 2)

def test_missing_usage_counts_zero_and_warns(caplog): ...
def test_unpriced_model_counts_zero_and_warns(caplog): ...

def test_usage_from_gathered_tasks_lands_in_one_ledger():
    async def call(p):
        with trial.purpose(p):
            trial.record_usage(gpt_oss_id, {"prompt_tokens": 1, "completion_tokens": 1})
    with trial.recording(ledger):
        asyncio.run(asyncio.gather(call("guard"), call("rewrite")))  # wrap in an async main
    assert ledger.tokens("guard") == (1, 1) and ledger.tokens("rewrite") == (1, 1)

def test_cost_log_line_has_fields_and_no_text():
    line = trial.cost_log_line("chat", "answered", "qa-1", sonnet_id, ledger, 12345)
    for field in ("kind=chat", "outcome=answered", "session=qa-1", f"model={sonnet_id}",
                  "reply_in=", "reply_out=", "guard_in=", "guard_out=",
                  "rewrite_in=", "rewrite_out=", "embed_in=", "turn_usd=", "total_usd=0.012345"):
        assert field in line

def test_models_payload_groups_in_order_with_default(): ...
```

- [ ] **Step 2: Run** `cd chat-ui && python -m pytest tests/test_trial.py -v`. Expected: FAIL (no module `trial`).
- [ ] **Step 3: Implement `chat-ui/trial.py`** with the interfaces above and a module docstring saying it is trial-only and how to remove it (delete the module, the `index.html` and `styles.css` blocks, and the `trial` calls in `main.py` and `inference.py`, then set `CHAT_MODEL` to the winner). USD values in log lines use 6 decimals.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Add trial model list, prices and cost ledger`.

### Task 2: `inference.py` — report usage and accept per-model params

**Files:**
- Modify: `chat-ui/inference.py` (`embed`, `chat`)
- Test: `chat-ui/tests/test_inference.py`

**Interfaces:**
- Consumes: `trial.record_usage(model, usage)`.
- Produces: `chat(config, messages, *, temperature=0.2, max_tokens=2048, top_p=1.0, model=None, params: dict | None = None, client=None) -> str`. `params` is merged over the payload last, and a key whose value is `None` is removed from the payload (e.g. `{"top_p": None, "max_tokens": None, "max_completion_tokens": 8192}`).

- [ ] **Step 1: Write the failing tests** in the file's existing `httpx.MockTransport` style:
  - `test_chat_records_usage_in_active_ledger`: the response has `"usage": {"prompt_tokens": 12, "completion_tokens": 3}`. Inside `trial.recording(ledger)` and `trial.purpose("reply")`, `ledger.tokens("reply") == (12, 3)`.
  - `test_embed_records_prompt_tokens`: the usage has `prompt_tokens: 7` and no `completion_tokens`, so the `"embed"` purpose gives `(7, 0)`, counted once per batch.
  - `test_chat_without_usage_still_returns_content`: the content is returned and the ledger total is 0.
  - `test_chat_params_override_and_remove_keys`: given `params={"top_p": None, "max_tokens": None, "max_completion_tokens": 8192}`, the sent JSON has no `top_p` or `max_tokens`, and `max_completion_tokens == 8192`.
- [ ] **Step 2: Run** `python -m pytest tests/test_inference.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** After a successful `_post`, call `record_usage(<model sent>, data.get("usage"))` in `chat` and in each `embed` batch. Apply `params` as specified.
- [ ] **Step 4: Run** `python -m pytest -v` (the whole suite). Expected: PASS.
- [ ] **Step 5: Commit** `feat: Record token usage and allow per-model request params`.

### Task 3: `history_signing.py` — sign the model and total

**Files:**
- Modify: `chat-ui/history_signing.py`
- Test: `chat-ui/tests/test_history_signing.py`

**Interfaces:**
- Produces:
  - `sign_history(key, history, session_id, *, model: str, total_microusd: int, now=None) -> str`
  - `verify_history(key, history, session_id, token, *, model: str, total_microusd: int, now=None) -> None`
  - The MAC payload adds `"m": model, "c": total_microusd`. **The empty-history skip is removed**: every call now needs a token. `main` decides when a request needs no token (Task 4).
  - `verify_history` raises `HistoryTokenError` if `model` is not a `str` or `total_microusd` is not an `int` (a `bool` doesn't count), and never `TypeError`.

- [ ] **Step 1: Update the existing tests** to pass `model="m1", total_microusd=0`. Replace `test_empty_history_needs_no_token` and `test_only_an_empty_list_skips_verification` with `test_empty_history_needs_a_token`. Add `test_rejects_changed_model`, `test_rejects_changed_total`, and `test_rejects_non_int_total` (parametrize `1.0`, `"5"`, `True`, `None`).
- [ ] **Step 2: Run** `python -m pytest tests/test_history_signing.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**, and update the module docstring to say the token also binds the conversation's model and running total.
- [ ] **Step 4: Run** the same command. Expected: PASS. (`main` calls are fixed in Task 4. Other suites may fail until then.)
- [ ] **Step 5: Commit** `feat: Bind the model and running cost into the history token`.

### Task 4: `main.py` — model choice, lock, cost and logging

**Files:**
- Modify: `chat-ui/main.py`
- Test: `chat-ui/tests/test_chat_pipeline.py`, `chat-ui/tests/test_upload.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces (the HTTP contract used by Task 6):
  - `GET /api/models` → `trial.models_payload(_config.chat_model)`.
  - `POST /api/upload` takes an optional form field `model` (default `_config.chat_model`). An unknown model gives 400 before `check_cv` runs. The check runs under a fresh `Ledger` with `purpose("guard")`. A rejected CV logs `cost_log_line("upload", "rejected", None, model, ledger, ledger.microusd())` and returns 400 as now. On success, the CV is stored with `"model"`. The response adds `model`, `total_microusd` and `history_token` (signed over `[]`, the session id, the model and the total), and the upload is logged with outcome `accepted`.
  - `POST /api/chat` body adds `model` and `total_microusd`. Every response (answered, blocked, error) adds `session_id`, `model`, `total_microusd` and `history_token`. The `expired` response stays a 400 with `{"error", "expired": true}`.

Request resolution in `chat_endpoint`, decided here:

1. **New Q&A conversation:** `history == []`, no `history_token` and no `session_id`. The model is the body's `model` or `_config.chat_model` (400 if unknown). The session id is `"qa-" + uuid4`, and the total is 0.
2. **Otherwise:** call `verify_history(..., model=body model, total_microusd=body total)`. Any `HistoryTokenError` means expired. A session id that doesn't start with `qa-` and isn't in `_cv_sessions` (a lost CV session) also means expired.
3. Run `_answer` under a fresh `Ledger`, passing the model through to the reply. The message check is wrapped in `purpose("guard")`, `rewrite_queries` in `purpose("rewrite")`, `embed` in `purpose("embed")`, and the reply `chat` in `purpose("reply")`, called with `model=<id>, params=find_model(id).params`.
4. Set `new_total = total + ledger.microusd()`. Log `cost_log_line("chat", outcome, ...)` where outcome is `answered`, `blocked` or `error`. Sign the returned history (updated if answered, unchanged otherwise) with `new_total`.

- [ ] **Step 1: Update the existing tests** to the new contract:
  - Signing helpers pass `model` and `total_microusd`.
  - `test_chat_with_unknown_session_sends_no_cv` becomes `test_chat_with_lost_cv_session_is_expired`.
  - `test_blocked_turn_returns_unchanged_history_and_token` now asserts the history is unchanged and the token is re-signed with the new total.
  - `test_chat_error_returns_unchanged_history` does the same.
- [ ] **Step 2: Write the new failing tests.** In the fixture, `fake_chat` and the other fakes call `trial.record_usage(...)` with fixed usage so totals are predictable.
  - `test_models_endpoint_lists_groups_and_default`
  - `test_first_qa_turn_creates_session_and_uses_chosen_model`: the reply call got `model=<sonnet id>`, and `session_id` starts with `qa-`
  - `test_guardrail_and_rewrite_use_fixed_model` (they don't receive the chosen model)
  - `test_unknown_model_is_rejected_with_400`
  - `test_total_accumulates_across_turns` (turn 2's total > turn 1's total > 0)
  - `test_changing_model_mid_conversation_is_expired`
  - `test_tampered_total_is_expired` (parametrize `+1`, a float and a string)
  - `test_blocked_first_qa_turn_returns_session_and_token_and_counts_cost`
  - `test_turn_log_line_has_cost_and_no_message_text` (caplog)
  - `test_upload_with_unknown_model_skips_cv_check`
  - `test_upload_returns_model_total_and_token_and_first_chat_accepts_them`
  - `test_rejected_upload_logs_cost_without_session`
- [ ] **Step 3: Run** `python -m pytest -v`. Expected: the new and updated tests FAIL.
- [ ] **Step 4: Implement** as described. Keep `_answer`'s current shape and add a `model: TrialModel` parameter.
- [ ] **Step 5: Run** `python -m pytest -v`. Expected: all PASS.
- [ ] **Step 6: Commit** `feat: Let each conversation choose a model and track its cost`.

### Task 5: Verify the models against DigitalOcean

**Files:**
- Create: `chat-ui/check_models.py` (a manual script, not a test)
- Modify: `chat-ui/trial.py` (real IDs, `params`)

**Interfaces:**
- Consumes: `InferenceConfig.from_env()`, `chat(..., model, params)`, `trial.MODELS`.

- [ ] **Step 1: Write `check_models.py`.** It does `GET {base_url}/models` with the key and prints which `trial.MODELS` IDs are missing, with close matches by substring. Then, for each model, it sends one chat, "Reply with the word OK.", using the same `max_tokens`/`params` the app uses for replies, and prints `OK`, `EMPTY` or `ERROR <status/short body>`, plus the `usage` returned. It also prints whether Kimi K3's DigitalOcean model card or `/models` entry says it has open weights, if that information is exposed.
- [ ] **Step 2: Run** `docker compose --profile tools run --rm evals python /app/check_models.py`. This uses `.env`, whose key may first need widening to all ten models in the DigitalOcean console. Expected: a line per model.
- [ ] **Step 3: Fix the IDs** in `trial.py` from the listing. For `ERROR` or `EMPTY` results, add `params` (e.g. drop `top_p`, use `max_completion_tokens`, raise the output budget for reasoning models) and re-run until every model is `OK`.
- [ ] **Step 4: Stop and ask the user** if any model is missing from `/models` or can't be made to return `OK`: drop it or replace it? Also ask if Kimi K3 turns out to be closed weights; the spec says to move it to "Premium · closed", so confirm it moved.
- [ ] **Step 5: Run** `cd chat-ui && python -m pytest -v`. Expected: PASS (tests must refer to model IDs via `trial.MODELS`, not literals).
- [ ] **Step 6: Commit** `chore: Use DigitalOcean's model IDs and per-model request params`.

### Task 6: Page — picker, status strip, lock and expiry

**Files:**
- Modify: `chat-ui/static/index.html`, `chat-ui/static/styles.css`

**Interfaces:**
- Consumes: the Task 4 HTTP contract.

Behaviour (layout A from the mockup):
- On load, fetch `/api/models`. Fill a `<select id="modelSelect">` with one `<optgroup label>` per group and select `default`. Put it inside `.landing__intro`, after the description, with the visible label "AI model for this conversation". If the fetch fails, show the select with a single default option named after the model id.
- Starting a conversation (`startGeneralQA` or a successful upload) disables the select. A strip `<div class="trial-strip" id="trialStrip">` under the site header shows `Model: <name> 🔒` on the left and `Conversation cost so far: $X.XXXX` on the right (`total_microusd / 1e6`, `toFixed(4)`). The cost text is `aria-live="polite"`.
- The upload sends `model` in the `FormData`. Each chat request sends `model` and `total_microusd`. After every response, store `session_id`, `model`, `total_microusd` and `history_token`, and update the strip.
- `backToStart()` hides the strip, clears the model and total state, and re-enables the select, which keeps its last value.
- **Expiry** (`data.expired`): call `backToStart()`, then show `data.error` in a `<p class="landing__notice" role="status">` on the landing page. The old "keep the CV session" branch is deleted. The notice clears when a new conversation starts.
- Wrap the new markup, script and CSS in `<!-- Model trial: remove after the trial -->` / `/* Model trial */` comments.

- [ ] **Step 1: Implement** the behaviour above.
- [ ] **Step 2: Verify in the browser** with `docker compose up chat-ui`, then open `http://localhost:8080` (use the `run` skill or claude-in-chrome). Check that:
  - the grouped dropdown shows 10 models and gpt-oss-20b is preselected;
  - choosing Claude Haiku 5.5, then "Ask a question" and two messages shows the locked strip with a growing non-zero cost;
  - Back unlocks the dropdown and hides the strip;
  - a CV upload with Sonnet shows a non-zero cost before the first message (the CV check);
  - the container logs show one `Turn cost:` line per turn with no message text.
  
  Take a screenshot of the strip.
- [ ] **Step 3: Commit** `feat: Add the model picker and conversation cost strip`.

### Task 7: Docs and deploy text

**Files:**
- Modify: `deploy/variables.tf:77`, `README.md` (key-scoping steps near line 113 and the architecture description near lines 11 and 29–36)

- [ ] **Step 1:** Change the `model_access_key` description, and the README's "Select models" step, to say the key must cover the ten trial models plus Qwen3 Embedding 0.6B (list them). In the README, describe the trial picker, cost strip and `Turn cost:` log lines in a short "Model trial (temporary)" section that says how to remove it (the `trial.py` docstring has the details).
- [ ] **Step 2: Run** `cd deploy && terraform fmt -check` and `cd ../chat-ui && python -m pytest -v`. Expected: both clean.
- [ ] **Step 3: Commit** `docs: Describe the model trial and widen the model key scope`.
