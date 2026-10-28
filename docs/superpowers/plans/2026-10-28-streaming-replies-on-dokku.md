# Streaming Replies on Dokku Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every trial model returns a complete answer. The answer streams to the browser with a status line that changes every 5s while the model thinks. The app is hosted on a Terraform-provisioned Dokku droplet instead of App Platform.

**Architecture:**
- **Replies:** `inference.chat_stream` reads DigitalOcean's streamed completion and yields thinking and answer pieces separately. A cut-off or empty reply raises `InferenceError`.
- **The chat endpoint:** `/api/chat` validates the request as today, then returns newline-delimited JSON events. These come from a per-turn task (`main._run_turn`), which runs the check and retrieval, streams the reply, and runs a `status.StatusFeed`. The feed summarises new thinking with gpt-oss-20b every 5s.
- **The browser:** reads the stream and draws a status line with a timer, then the streaming answer.
- **Hosting:** Terraform creates a LON1 droplet with a reserved IP and a firewall. Cloud-init installs and configures Dokku. A GitHub Actions workflow deploys every push to main.

**Tech Stack:** Python 3.14, FastAPI 0.115 / Starlette 0.38.6, httpx 0.27, vanilla JS, Terraform (digitalocean ~> 2.106, random, tls), cloud-init, Dokku with dokku-letsencrypt, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-28-streaming-replies-on-dokku-design.md`

## Global Constraints

- No new Python dependencies (`chat-ui/requirements.txt` is unchanged).
- Reply `max_tokens` = 32768. Message check (`CHECK_MAX_TOKENS`) and query rewrite = 4096. The check and rewrite keep their current reasoning effort.
- Summariser: `config.chat_model` (gpt-oss-20b), `reasoning_effort: "low"`. Interval 5s. Status lines at most 80 characters, with no links or markup.
- Streaming idle timeout (silence between chunks) = 60s.
- Never log message text, CV text, thinking text or status text. Log lines carry counts, reasons and ids only.
- Event types are exactly `status`, `answer`, `done` and `error`, one JSON object per line. Media type `application/x-ndjson`.
- The server never sends `status` before the message check passes. A blocked message streams only `done`.
- User-facing failure copy: `"Sorry, something went wrong while writing that reply. Please try sending your message again."`
- Hosting values:
  - domain: `evenbreak-cv.businesstime.team`
  - droplet: region `lon1`, size with 2GB RAM
  - firewall inbound: only 22, 80 and 443
  - nginx: `proxy-read-timeout 600s`, `proxy-buffering off`
- Commit messages follow the repo style: `feat: Sentence in plain words`, `fix: …`, `docs: …`.
- Python tests run with `docker compose run --rm chat-ui-test` (the whole suite) or `docker compose run --rm chat-ui-test python -m pytest tests/<file>::<test> -v`. The eval tests run with `docker compose --profile tools run --rm evals python -m pytest /evals/tests -v`.

## Review Focus

1. **CV uploads over 1MB:** Dokku's nginx defaults to `client_max_body_size 1m`, so a typical PDF CV would get a 413 error. Expect uploads to behave as on App Platform. Task 7 sets `client-max-body-size 10m`, and Task 9 uploads a 2–5MB CV on the droplet.
2. **The model key in build or deploy logs:** passing `MODEL_ACCESS_KEY` as a Docker build argument through Dokku must not print it in the GitHub Actions deploy log. Task 8 checks the log for the key's prefix.
3. **The browser leaves mid-thinking** (tab closed, new conversation): the reply call and summary calls must stop, and the turn is logged `outcome=abandoned`. Task 4 tests `_run_turn` cancellation. Task 9 checks it for real with `curl` and Ctrl-C.
4. **A CV that steers the summariser:** status text must never show a URL, markdown, HTML or a long quoted passage. Task 3's `clean_status` tests pin this.
5. **The provider goes silent mid-stream:** the turn must fail with an `error` event after 60s, not hang. httpx's read timeout isn't enforced under `MockTransport`, so `chat_stream` enforces idle time itself, and Task 2 tests it.

---

### Task 1: Cut-off replies are failures, with higher limits for the background calls

**Files:**
- Modify: `chat-ui/inference.py` (`chat`)
- Modify: `chat-ui/guardrails.py:33-36` (`CHECK_MAX_TOKENS` and its comment)
- Modify: `chat-ui/prompting.py:72` (the rewrite's `max_tokens=512`)
- Test: `chat-ui/tests/test_inference.py`, `chat-ui/tests/test_prompting.py`

**Interfaces:**
- Produces: `inference.chat(...)` raises `InferenceError` when `choices[0].finish_reason` is present and isn't `"stop"`. Usage is still recorded before raising. `prompting.REWRITE_MAX_TOKENS = 4096`. `guardrails.CHECK_MAX_TOKENS = 4096`.

- [ ] **Step 1: Write the failing tests** in `tests/test_inference.py`. Each test builds its mock response like the existing `_chat_response`, plus a `finish_reason` on the choice.

```python
def test_chat_raises_when_reply_is_cut_off():
    # 200 response, choices[0] = {"message": {"content": "partial"}, "finish_reason": "length"}
    with pytest.raises(InferenceError, match="length"):
        asyncio.run(inference.chat(CONFIG, [], client=_client(handler)))

def test_chat_cut_off_reply_still_records_usage():
    # same response with usage {"prompt_tokens": 10, "completion_tokens": 2048}, inside trial.recording(ledger)
    assert ledger.tokens("other") == (10, 2048)

def test_chat_accepts_stop_and_missing_finish_reason():
    # "stop" -> returns content; no finish_reason key -> returns content
```

Add to `tests/test_prompting.py`:

```python
def test_rewrite_falls_back_to_raw_message_on_cut_off_reply(monkeypatch):
    # monkeypatch prompting.chat to raise InferenceError("chat finished with length")
    assert asyncio.run(prompting.rewrite_queries(CONFIG, "my question", [], None)) == ["my question"]

def test_rewrite_uses_its_token_limit(monkeypatch):
    # spy on prompting.chat; assert kwargs["max_tokens"] == prompting.REWRITE_MAX_TOKENS == 4096
```

- [ ] **Step 2: Run them and check they fail.**
Run: `docker compose run --rm chat-ui-test python -m pytest tests/test_inference.py tests/test_prompting.py -v`
Expected: the new tests FAIL. (The fallback test may already pass; the others fail on the missing raise and the missing constant.)

- [ ] **Step 3: Implement.**
  - `chat`: after `record_usage`, read `data["choices"][0].get("finish_reason")`, and raise `InferenceError(f"chat finished with {reason}")` when it's set and not `"stop"`.
  - `prompting.py`: add `REWRITE_MAX_TOKENS = 4096` and pass it.
  - `guardrails.py`: set `CHECK_MAX_TOKENS = 4096`. Update the comment to say that gpt-oss's reasoning varies a lot and that a cut-off reply is treated as a failed check.

- [ ] **Step 4: Run the whole suite.**
Run: `docker compose run --rm chat-ui-test`
Expected: all pass. `test_guardrails.py` still passes, because a cut-off check reply now fails closed through the existing `InferenceError` path.

- [ ] **Step 5: Commit**

```bash
git add chat-ui/inference.py chat-ui/guardrails.py chat-ui/prompting.py chat-ui/tests/test_inference.py chat-ui/tests/test_prompting.py
git commit -m "fix: Treat a cut-off model reply as a failure and raise the check and rewrite limits"
```

### Task 2: Streamed chat completions

**Files:**
- Modify: `chat-ui/inference.py`
- Test: `chat-ui/tests/test_inference.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class ChatDelta:
    kind: Literal["thinking", "answer"]
    text: str

IDLE_TIMEOUT_SECONDS = 60.0

async def chat_stream(
    config: InferenceConfig, messages: list[dict], *,
    temperature: float = 0.2, max_tokens: int = 2048, top_p: float = 1.0,
    model: str | None = None, params: dict | None = None,
    client: httpx.AsyncClient | None = None,
    idle_timeout: float = IDLE_TIMEOUT_SECONDS,
) -> AsyncIterator[ChatDelta]
```

- Behaviour:
  - **Request:** the same payload as `chat`, built by a shared helper so the `params` override and removal rules apply to both, plus `"stream": True, "stream_options": {"include_usage": True}`.
  - **Pieces:** `delta.reasoning_content` → `ChatDelta("thinking", …)`, `delta.content` → `ChatDelta("answer", …)`, skipping empty strings.
  - **Usage:** the usage chunk (its `choices` may be empty) is passed to `trial.record_usage` once, after the stream ends, before any end-of-stream error is raised.
  - **Failures, all `InferenceError`:**
    - an HTTP status ≥400 (read the body, then `"chat/completions returned {code}: {body[:500]}"`)
    - a transport error
    - a line that isn't valid JSON
    - no chunk for `idle_timeout` seconds (enforced with `asyncio.wait_for` around each line read, not only via httpx)
    - a final `finish_reason` other than `"stop"`, or no `finish_reason` at all
    - a stream with no answer text
  - **Timeouts:** the owned client uses `httpx.Timeout(10.0, read=idle_timeout)`. `TIMEOUT_SECONDS` stays for the non-streaming calls.

- [ ] **Step 1: Write the failing tests.** Use a `MockTransport` handler that returns `httpx.Response(200, stream=...)` with SSE lines (`data: {...}\n\n`, ending with `data: [DONE]`). For the idle test, use an async byte iterator that sleeps.

```python
def test_chat_stream_yields_thinking_then_answer():
    assert deltas == [ChatDelta("thinking", "Hmm"), ChatDelta("answer", "Hi"), ChatDelta("answer", " there")]

def test_chat_stream_sends_stream_options_and_params():
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 32768  # passed by the caller
    # a params override and a None removal behave as in test_chat_params_override_and_remove_keys

def test_chat_stream_records_usage_from_final_chunk():
    assert ledger.tokens("other") == (600, 6000)

def test_chat_stream_raises_on_length_after_recording_usage():
    with pytest.raises(InferenceError, match="length"): ...
    assert ledger.tokens("other") == (600, 32768)

def test_chat_stream_raises_when_no_answer_text():          # only thinking, finish "stop"
def test_chat_stream_raises_when_stream_ends_without_finish_reason():
def test_chat_stream_raises_on_http_error():                # 429 body "busy" -> match "429"
def test_chat_stream_raises_on_malformed_line():            # "data: {not json"
def test_chat_stream_raises_after_idle_timeout():           # idle_timeout=0.05, iterator sleeps 0.2s -> InferenceError
```

- [ ] **Step 2: Run them and check they fail.**
Run: `docker compose run --rm chat-ui-test python -m pytest tests/test_inference.py -v -k chat_stream`
Expected: FAIL with `AttributeError: module 'inference' has no attribute 'chat_stream'`.

- [ ] **Step 3: Implement `ChatDelta`, `IDLE_TIMEOUT_SECONDS` and `chat_stream`** in `inference.py`. Use `client.stream("POST", …)` and `aiter_lines()`, ignoring blank lines and anything that doesn't start with `data:`. Update the module docstring to mention streaming.

- [ ] **Step 4: Run the tests and check they pass.**
Run: `docker compose run --rm chat-ui-test python -m pytest tests/test_inference.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add chat-ui/inference.py chat-ui/tests/test_inference.py
git commit -m "feat: Stream chat completions, separating thinking from the answer"
```

### Task 3: Status summariser

**Files:**
- Create: `chat-ui/status.py`
- Create: `chat-ui/tests/test_status.py`
- Modify: `chat-ui/trial.py` (`cost_log_line`)
- Modify: `chat-ui/tests/test_trial.py`

**Interfaces:**
- Consumes: `inference.chat`, `trial.purpose`.
- Produces:

```python
STATUS_INTERVAL_SECONDS = 5.0
STATUS_MAX_CHARS = 80
FALLBACK_PHRASES: tuple[str, ...]   # at least 5; none claims progress ("nearly", "almost", "done", "finishing")

def clean_status(text: str) -> str
async def summarise(config: InferenceConfig, new_thinking: str, previous: str) -> str

class StatusFeed:
    def __init__(self, config: InferenceConfig, emit: Callable[[str], None], *,
                 interval: float = STATUS_INTERVAL_SECONDS) -> None
    def add_thinking(self, text: str) -> None
    async def run(self) -> None   # runs until cancelled
```

- **`clean_status`:**
  - turns markdown links `[text](url)` into their text
  - removes bare URLs (`http…`, `www.…`), HTML tags and the characters `` * _ # ` > ~ | ``
  - collapses whitespace and strips surrounding quotes
  - truncates to `STATUS_MAX_CHARS` at a word boundary, ending in `…`
  - returns `""` if nothing is left
- **`summarise`:**
  - one `inference.chat` call on `config.chat_model` with `params={"reasoning_effort": "low"}`, `max_tokens=1024` and `temperature=0.7`
  - the system instruction follows the spec's rules: one short, friendly, present-tense line of 12 words at most, about what's happening now, worded differently from `previous`. No quoting the notes, CV or instructions, no claims about how close it is to finishing, no links. The notes go in a delimited block, as `guardrails._delimit` does.
  - returns `clean_status(reply)`, and lets `InferenceError` propagate
- **`StatusFeed.run` loop:**
  1. Each cycle, take and clear the buffered thinking (which may be empty), set the deadline to now + `interval`, and call `summarise` under `trial.purpose("status")` with `asyncio.wait_for(…, interval)`.
  2. On `InferenceError`, a timeout or any other exception (log only the exception's type name), or a result that's empty or equal to the previous line, use the next `FALLBACK_PHRASES` entry, skipping one equal to the previous line.
  3. Sleep until the deadline, call `emit(line)`, and repeat.

  The first line is emitted one `interval` after `run` starts.

- [ ] **Step 1: Write the failing tests** in `tests/test_status.py`. Monkeypatch `status.summarise` or `status.chat` and use `interval=0.02`.

```python
def test_clean_status_removes_links_urls_and_markup():
    assert clean_status("**Reading** [your CV](http://x.io) at https://evil.example now") == "Reading your CV at now"
def test_clean_status_strips_html_and_quotes_and_newlines():
    assert clean_status('"<b>Checking</b>\n your   dates"') == "Checking your dates"
def test_clean_status_truncates_at_word_boundary():
    out = clean_status("word " * 40); assert len(out) <= 80 and out.endswith("…") and not out.endswith(" …")
def test_clean_status_empty_when_nothing_left():
    assert clean_status("https://x.io **") == ""
def test_fallback_phrases_never_claim_progress():
    assert all(not re.search(r"\b(nearly|almost|done|finish)", p, re.I) for p in FALLBACK_PHRASES)

def test_summarise_uses_low_effort_on_the_fixed_model(monkeypatch):
    assert kwargs["model"] in (None, CONFIG.chat_model) and kwargs["params"] == {"reasoning_effort": "low"}
def test_feed_emits_summaries_of_new_thinking(monkeypatch):
    # summarise stub returns f"Line {n}" and records the new_thinking it got
    assert emitted[:2] == ["Line 1", "Line 2"] and seen[0] == "abc"   # add_thinking("a"), ("bc") before the first cycle
def test_feed_never_repeats_a_line(monkeypatch):           # stub always returns "Same"
    assert all(a != b for a, b in zip(emitted, emitted[1:]))
def test_feed_falls_back_when_summary_fails(monkeypatch):  # stub raises InferenceError
    assert emitted[0] in FALLBACK_PHRASES
def test_feed_falls_back_when_summary_is_slow(monkeypatch):  # stub sleeps 1s, interval 0.02
    assert emitted[0] in FALLBACK_PHRASES
def test_feed_summary_cost_lands_in_the_ledger_as_status(monkeypatch):
    # stub calls trial.record_usage(CONFIG.chat_model, {"prompt_tokens": 450, "completion_tokens": 50})
    assert ledger.tokens("status") == (450, 50)   # run() started as a task inside trial.recording(ledger)
```

In `tests/test_trial.py`, extend `test_cost_log_line_has_fields_and_no_text` to assert that `status_in=` and `status_out=` appear after `rewrite_out=`.

- [ ] **Step 2: Run them and check they fail.**
Run: `docker compose run --rm chat-ui-test python -m pytest tests/test_status.py tests/test_trial.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'status'`, plus the `cost_log_line` assertion.

- [ ] **Step 3: Implement `status.py` and add `status_in`/`status_out`** to `trial.cost_log_line`, between `rewrite_out` and `embed_in`.

- [ ] **Step 4: Run the tests and check they pass.**
Run: `docker compose run --rm chat-ui-test python -m pytest tests/test_status.py tests/test_trial.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add chat-ui/status.py chat-ui/tests/test_status.py chat-ui/trial.py chat-ui/tests/test_trial.py
git commit -m "feat: Summarise a model's thinking into a status line every 5 seconds"
```

### Task 4: `/api/chat` streams events

**Files:**
- Modify: `chat-ui/main.py` (the `_answer` and `chat_endpoint` flow, the docstring, and the `_cv_sessions` comment: "App Platform" becomes "single Dokku instance")
- Modify: `chat-ui/tests/test_chat_pipeline.py`

**Interfaces:**
- Consumes: `inference.chat_stream`, `ChatDelta` (Task 2), `status.StatusFeed`, `STATUS_INTERVAL_SECONDS` (Task 3).
- Produces:
  - `main.REPLY_MAX_TOKENS = 32768`
  - `main.REPLY_FAILED_MESSAGE` (the copy in Global Constraints)
  - `async def _run_turn(emit: Callable[[dict], None], *, message: str, received: list, session_id: str, cv: dict | None, model: TrialModel, total: int) -> None`
  - The response contract:
    - Validation failures stay the same JSON 400 responses as today.
    - Otherwise the response is a `StreamingResponse` with `media_type="application/x-ndjson"` and headers `Cache-Control: no-store` and `X-Accel-Buffering: no`, made of these events:
      - `{"type": "status", "text": str}`
      - `{"type": "answer", "text": str}`
      - `{"type": "done", "content": str, "blocked": str | None, "sources": [...], **_conversation(...)}`
      - `{"type": "error", "error": REPLY_FAILED_MESSAGE, **_conversation(received, ...)}`
- **`_run_turn` behaviour:**
  1. Make a `trial.Ledger()` and enter `trial.recording` inside the task.
  2. Run the check and retrieval together exactly as `_answer` does today; a block emits only `done` with the canned reply.
  3. Stream the reply with `chat_stream(_config, messages, model=model.id, params=model.params, max_tokens=REPLY_MAX_TOKENS)` under `trial.purpose("reply")`.
  4. Start one `StatusFeed(_config, emit_status, interval=STATUS_INTERVAL_SECONDS)` task on the first thinking piece, and feed it every thinking piece.
  5. Cancel the feed on the first answer piece and whenever the turn ends.
  6. Emit each answer piece. At the end, emit `done` with the joined answer and the updated signed state.
  7. On `InferenceError` or any other exception, log it as today (the exception's type name only for unexpected ones) and emit `error`, with the history unchanged and the total including this turn's cost.
  8. On `asyncio.CancelledError`, cancel the feed, log the cost line with `outcome=abandoned`, and re-raise.
  9. Every path logs exactly one `Turn cost:` line.
- **The endpoint:** builds an `asyncio.Queue`, starts `_run_turn(queue.put_nowait, …)` as a task, and yields `json.dumps(event) + "\n"` until a `done` or `error` event. Its `finally` cancels the task if it's still running. (Starlette 0.38.6 cancels the generator when the client disconnects, which reaches `_run_turn` as `CancelledError`.)

- [ ] **Step 1: Rework the test fixture and helpers.**
  - Replace `fake_chat` with `fake_chat_stream(config, messages, **kwargs)`, an async generator that:
    - records `messages` and `kwargs`
    - yields `calls.get("deltas", [ChatDelta("answer", "stubbed answer")])`, with `await asyncio.sleep(calls.get("delta_sleep", 0))` between pieces
    - raises `calls["chat_error"]` after yielding `calls.get("deltas_before_error", [])`
    - records `REPLY_USAGE`
  - Monkeypatch `main.chat_stream`, `main.STATUS_INTERVAL_SECONDS = 0.01`, and `status.summarise` to a stub that records `STATUS_USAGE = {"prompt_tokens": 450, "completion_tokens": 50}` and returns `f"Status {n}"`.
  - Add `_events(resp) -> list[dict]` (parse the NDJSON body) and `_final(resp) -> dict` (the last event).
  - Change every existing test that reads a 200 body from `resp.json()` to `_final(resp)`. Keep `resp.json()` for 400s. Point `_next(body, …)` at the final event.

- [ ] **Step 2: Write the new failing tests.**

```python
def test_chat_streams_answer_pieces_then_done(pipeline):
    pipeline["deltas"] = [ChatDelta("answer", "Hello"), ChatDelta("answer", " world")]
    assert [e["type"] for e in events] == ["answer", "answer", "done"]
    assert events[-1]["content"] == "Hello world" and events[-1]["history"][-1]["content"] == "Hello world"

def test_chat_stream_is_ndjson_and_unbuffered(pipeline):
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert resp.headers["x-accel-buffering"] == "no"

def test_thinking_produces_changing_status_events_before_the_answer(pipeline):
    pipeline["deltas"] = [ChatDelta("thinking", "t")] * 5 + [ChatDelta("answer", "A")]; pipeline["delta_sleep"] = 0.03
    types = [e["type"] for e in events]
    assert "status" in types and types.index("status") < types.index("answer")
    assert "status" not in types[types.index("answer"):]
    texts = [e["text"] for e in events if e["type"] == "status"]; assert all(a != b for a, b in zip(texts, texts[1:]))

def test_blocked_message_streams_only_done(pipeline):
    pipeline["verdict"] = Verdict(False, "off_topic")
    assert [e["type"] for e in events] == ["done"] and events[0]["blocked"] == "off_topic"

def test_reply_failure_streams_plain_error_with_unchanged_history(pipeline):
    pipeline["chat_error"] = InferenceError("chat/completions returned 429: busy")
    final = _final(_chat({"message": "Go on", **_signed(history, total=500)}))
    assert final["type"] == "error" and final["error"] == main.REPLY_FAILED_MESSAGE
    assert "429" not in final["error"] and final["history"] == history and final["total_microusd"] > 500

def test_failure_after_partial_answer_is_an_error(pipeline):
    pipeline["deltas_before_error"] = [ChatDelta("answer", "Half")]; pipeline["chat_error"] = InferenceError("chat finished with length")
    assert _final(resp)["type"] == "error" and _final(resp)["history"] == []

def test_reply_uses_reply_max_tokens(pipeline):
    assert pipeline["chat_kwargs"][-1]["max_tokens"] == 32768

def test_status_cost_is_in_total_and_log_line(pipeline, caplog):
    # thinking deltas as above
    assert "status_in=" in line and "status_in=0" not in line

def test_unexpected_crash_streams_error_instead_of_hanging(pipeline, monkeypatch):
    monkeypatch.setattr(main, "build_messages", boom)
    assert _final(resp)["type"] == "error"

def test_abandoned_turn_cancels_the_reply_and_logs_abandoned(pipeline, caplog):
    # fake stream yields one thinking piece then awaits a never-set Event; record CancelledError in calls
    # asyncio.run: start _run_turn(events.append, ...) as a task, wait until a thinking piece was consumed, cancel it
    assert pipeline["stream_cancelled"] is True
    assert "outcome=abandoned" in caplog.text and "secret" not in caplog.text
```

Delete `test_chat_inference_error_returns_readable_error`; the plain-error test replaces it.

- [ ] **Step 3: Run them and check they fail.**
Run: `docker compose run --rm chat-ui-test python -m pytest tests/test_chat_pipeline.py -v`
Expected: the new tests FAIL (no NDJSON response yet). The reworked existing tests FAIL on parsing.

- [ ] **Step 4: Implement `_run_turn`, the streaming endpoint and the constants** in `main.py`. Replace `_answer` with a check-and-retrieve helper that returns `(blocked_reason, passages)` and leaves the reply call to `_run_turn`. Update the module and endpoint docstrings to describe the event stream.

- [ ] **Step 5: Run the whole suite.**
Run: `docker compose run --rm chat-ui-test`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add chat-ui/main.py chat-ui/tests/test_chat_pipeline.py
git commit -m "feat: Stream each chat turn as status, answer and final events"
```

### Task 5: Browser shows the status line and the streaming answer

**Files:**
- Modify: `chat-ui/static/index.html` (`sendMessage`, `backToStart`, `appendTyping` becomes a status-message builder)
- Modify: `chat-ui/static/styles.css` (status-line styles, using the existing `--eb-*` tokens; remove the now-unused typing-indicator rules)

**Interfaces:**
- Consumes: the event contract from Task 4.
- Behaviour (the spec's "What the user sees" section, option A):
  - **On send:** append an assistant message showing a status line (pulsing dot, text, " · Ns" timer) and start a 1s timer. Show `"Reading your message…"`, then rotate every 5s through a local list until the first server event. The phrases are `"Reading your message…"`, `"Finding the right guidance…"`, `"Getting ready to answer…"` and `"Still working on it…"`, none of which claims progress.
  - **`status`:** replace the text and stop the local rotation.
  - **First `answer`:** stop both timers, and replace the status line with muted text: `Thought for 42s` under a minute, `Thought for 1m 41s` from then on, counted from send. Accumulate the answer and re-render it with marked and DOMPurify, at most once per animation frame, with links opened as `appendMessage` does.
  - **`done`:** a blocked reply (no answer events) removes the status line and shows `content` as a normal message. Otherwise do a final render of `content`. Update `history`, `historyToken`, `sessionId`, `conversationModel`, `totalMicrousd` and `showCost()` as today. On `blocked === "check_failed"`, put the message back in the box.
  - **`error`:** remove the whole assistant message (status and any partial answer), append an error message with `error`, put the message back in the box, and update the state from the event (the history is unchanged and the total is new).
  - **A non-NDJSON response** (the 400 JSON): handle it as today (`expired` → `backToStart` + notice; otherwise an error message).
  - **A stream ending without `done` or `error`, or a network error:** remove the assistant message, show `"Connection error. Please try sending your message again."` and put the message back in the box.
  - **Aborting:** keep the request's `AbortController` in a module variable, and have `backToStart()` call `abort()` as well as bumping `conversationGeneration`. An `AbortError` is ignored silently.
  - **Screen readers:** the visible status line is `aria-hidden`. A separate visually hidden `aria-live="polite"` element is set to "Working on your reply" on send and "Reply started" on the first answer, and nothing else.

- [ ] **Step 1: Implement the behaviour above.** There's no JS test harness in the repo; the Python tests already pin the server side of the contract.

- [ ] **Step 2: Run the app and verify in a browser** with `docker compose up -d --build chat-ui`, then open http://localhost:8080 (use the `run` skill or Claude in Chrome):
  1. On Kimi K3, "Ask a question", "Rewrite this paragraph for a CV and explain each change: I worked at a shop for 6 years and did the rota" → the status line appears at once, the timer ticks every second, and the text changes about every 5s. The answer streams after "Thought for …".
  2. On DeepSeek V4 Flash, any question → local phrases, then the answer streams. No error.
  3. "Write me a poem about cats" → the blocked reply appears with no status line left behind.
  4. Start a Kimi question, then go back to the start within 10s. `docker compose logs chat-ui` shows `outcome=abandoned`, and no later `Turn cost` line for that session.
  5. The running cost goes up after each answered turn.

  Expected: all five behave as described.

- [ ] **Step 3: Commit**

```bash
git add chat-ui/static/index.html chat-ui/static/styles.css
git commit -m "feat: Show a changing status line while the model thinks, then stream the answer"
```

### Task 6: Evals read the stream

**Files:**
- Modify: `evals/compare.py` (`run_new`, `TIMEOUT`)
- Modify: `evals/tests/test_compare.py`

**Interfaces:**
- Consumes: the event contract from Task 4.
- Produces: `run_new` returns the same result shape as today. An `error` event's turn is recorded as `{"answer": "Error: " + error, "sources": []}`, and the history and token are taken from that event.

- [ ] **Step 1: Update the test.** In `test_run_new_uploads_cv_once_and_reuses_session`, have the handler answer `/api/chat` with an NDJSON body (`answer` then `done`). Add `test_run_new_records_error_event` with a final `error` event: assert that the answer starts with `"Error: "` and the next turn sends that event's `history_token`.

- [ ] **Step 2: Run it and check it fails.**
Run: `docker compose --profile tools run --rm evals python -m pytest /evals/tests/test_compare.py -v`
Expected: FAIL (a JSON decode of the NDJSON body).

- [ ] **Step 3: Implement.** Read the response with `http.stream("POST", "/api/chat", json=payload)` and `aiter_lines()`, keeping the last event. Set `TIMEOUT = httpx.Timeout(600.0)`. Update the docstring.

- [ ] **Step 4: Run the eval tests and check they pass.**
Run: `docker compose --profile tools run --rm evals python -m pytest /evals/tests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add evals/compare.py evals/tests/test_compare.py
git commit -m "feat: Read streamed chat replies in the comparison eval"
```

### Task 7: Terraform creates and sets up the Dokku droplet

**Files:**
- Create: `deploy/droplet.tf` (keys, droplet, reserved IP and assignment, firewall)
- Create: `deploy/cloud-init.yaml.tftpl`
- Modify: `deploy/provider.tf` (add `hashicorp/tls ~> 4.0`)
- Modify: `deploy/.terraform.lock.hcl` (via `terraform providers lock -platform=linux_amd64 -platform=darwin_arm64`)
- Modify: `deploy/variables.tf`, `deploy/outputs.tf`, `deploy/projects.tf` (add the droplet and reserved IP URNs to `digitalocean_project_resources`; the App Platform app stays until Task 10)
- Modify: `deploy/terraform.tfvars` (`letsencrypt_email`, `admin_ssh_public_keys`; the user supplies the values in Task 9, so leave them as clearly marked placeholders)

**Interfaces:**
- **Variables:**

  | Variable | Type | Default |
  |---|---|---|
  | `domain` | string | `"evenbreak-cv.businesstime.team"` |
  | `letsencrypt_email` | string | required |
  | `admin_ssh_public_keys` | list(string) | required |
  | `droplet_region` | string | `"lon1"` |
  | `droplet_size` | string | `"s-1vcpu-2gb"` |
  | `droplet_image` | string | an Ubuntu LTS slug that Dokku supports |
  | `dokku_version` | string | latest stable Dokku tag at implementation time; note the date checked in the description |
  | `dokku_letsencrypt_version` | string | pinned plugin tag |

  `model_access_key` and `random_password.history_signing_key` are reused.
- **Outputs:**
  - `reserved_ip`
  - `dokku_ssh_known_hosts`: `"${var.domain},${reserved_ip} ${host public key}"`
  - `dokku_deploy_private_key`: sensitive, the OpenSSH private key
- **Resources:**
  - `tls_private_key.deploy` and `tls_private_key.host` (both `ED25519`)
  - `digitalocean_droplet.chat`, with `user_data = templatefile(...)` so any change replaces it, and `monitoring = true`
  - `digitalocean_reserved_ip.chat` + `digitalocean_reserved_ip_assignment.chat`
  - `digitalocean_firewall.chat`: inbound TCP 22, 80 and 443 from `0.0.0.0/0` and `::/0`; outbound all TCP, UDP and ICMP
- **Cloud-init must, in this order:**
  1. Install the host key from `tls_private_key.host` (`ssh_keys` with `ed25519_private`/`ed25519_public`, and don't generate others). Put the admin keys in root's `ssh_authorized_keys`.
  2. Write `/etc/apt/apt.conf.d/52unattended-reboot` with `Unattended-Upgrade::Automatic-Reboot "true";` and `Unattended-Upgrade::Automatic-Reboot-Time "03:00";`, and make sure unattended-upgrades is enabled.
  3. Install Dokku `${dokku_version}` non-interactively (debconf preseeding as Dokku's docs describe), then install dokku-letsencrypt at `${dokku_letsencrypt_version}`.
  4. Set up the app `chat-ui`:
     - `apps:create`
     - `domains:set chat-ui ${domain}`
     - `git:set chat-ui deploy-branch main`
     - `builder-dockerfile:set chat-ui dockerfile-path chat-ui/Dockerfile`
     - `ports:set chat-ui http:80:8080`
     - a health check on `GET /health` (Dokku's app.json healthchecks or `checks` settings; builder's choice)
     - `config:set --no-restart chat-ui MODEL_ACCESS_KEY=… HISTORY_SIGNING_KEY=…`
     - `docker-options:add chat-ui build "--build-arg MODEL_ACCESS_KEY=…"`
     - `nginx:set chat-ui proxy-read-timeout 600s`
     - `nginx:set chat-ui proxy-buffering off`
     - `nginx:set chat-ui client-max-body-size 10m`
     - `letsencrypt:set chat-ui email ${letsencrypt_email}`
     - `letsencrypt:cron-job --add`
  5. **Last:** `ssh-keys:add deploy` with the deploy public key. The deploy workflow treats a working deploy key as "setup finished".

  Secrets reach the commands through a root-only file written by `write_files` (mode `0600`) and are read from there. They never appear in a command line or in `cloud-init-output.log`, and the file is deleted at the end.

- [ ] **Step 1: Write the configuration above.**

- [ ] **Step 2: Check it's valid.**
Run: `cd deploy && terraform fmt -check && terraform init -input=false && terraform validate`
Expected: `Success! The configuration is valid.`

- [ ] **Step 3: Plan against real state.** Fill the tfvars placeholders temporarily with test values, then run `cd deploy && source env.sh && terraform plan`.
Expected: the plan adds the two keys, the droplet, the reserved IP and assignment, and the firewall, and updates the project resources. It shows no destroy or replace of `digitalocean_app.chat_ui`.

- [ ] **Step 4: Commit**

```bash
git add deploy/
git commit -m "feat: Create a Dokku droplet in London with Terraform and set it up with cloud-init"
```

### Task 8: Deploy on push to main, and redeploy after Terraform applies

**Files:**
- Create: `.github/workflows/deploy.yml`
- Modify: `.github/workflows/terraform-apply.yml` (add `actions: write` to `permissions`, and a final step that runs `gh workflow run deploy.yml --ref main` with `GH_TOKEN: ${{ github.token }}`)
- Modify: `README.md` (architecture, the setup for a new server, and the secrets table), `deploy/env.sh.example` (if new `TF_VAR_*` secrets are added; none are expected)

**Interfaces:**
- Consumes: the Task 7 outputs. Repo variable `DOKKU_HOST` (the reserved IP). Repo secrets `DOKKU_SSH_PRIVATE_KEY` and `DOKKU_KNOWN_HOSTS`.
- **`deploy.yml`:**
  - triggered on `push` to `main` and on `workflow_dispatch`
  - `concurrency: {group: deploy, cancel-in-progress: false}`
  - `actions/checkout@v4` with `fetch-depth: 0`
  - set up `~/.ssh` from the secrets
  - wait up to 20 minutes, retrying every 15s, until `ssh dokku@$DOKKU_HOST apps:exists chat-ui` succeeds
  - deploy with `dokku/github-action` (pinned to a release tag) to `ssh://dokku@$DOKKU_HOST:22/chat-ui`
  - then `letsencrypt:active chat-ui || letsencrypt:enable chat-ui` as a step that warns instead of failing, because it fails until the DNS record exists
- **Deliberate departure from the spec:** the spec says the two workflows share one concurrency group. GitHub replaces a pending run in a group with a newer one, which could silently drop a queued Terraform apply. Instead:
  - deploys retry until the server is ready
  - every Terraform apply triggers a deploy when it finishes
  - a deploy that happens to hit a droplet being replaced is followed by that triggered deploy

  Note this in the workflow's header comment.

- [ ] **Step 1: Write both workflow changes and the README** (the "set up a new server" steps from Task 9 in short, and the outputs to copy into GitHub).

- [ ] **Step 2: Lint the workflows.**
Run: `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest`
Expected: no output (exit 0).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ README.md deploy/env.sh.example
git commit -m "feat: Deploy to Dokku on every push to main and after each Terraform apply"
```

### Task 9: Bring the droplet up and verify it (with the user)

No code. Each step needs the user or their credentials.

- [ ] **Step 1:** Ask the user for the Let's Encrypt email and the admin SSH public keys, and put them in `deploy/terraform.tfvars`.
- [ ] **Step 2:** Run `cd deploy && source env.sh && terraform apply`. Expected: the Task 7 resources are created, and `terraform output reserved_ip` prints an address.
- [ ] **Step 3:** The user adds an A record for `evenbreak-cv.businesstime.team` pointing at the reserved IP, and the GitHub secrets and variable from `terraform output` (`-raw dokku_deploy_private_key`, `dokku_ssh_known_hosts`, `reserved_ip`).
- [ ] **Step 4:** Run the deploy with `gh workflow run deploy.yml --ref <branch>` (or after merging). Expected: the run succeeds. Search its log for the first 8 characters of `MODEL_ACCESS_KEY` and expect no matches (Review Focus 2).
- [ ] **Step 5:** After DNS resolves (`dig +short evenbreak-cv.businesstime.team`), re-run the deploy. Expected: `https://evenbreak-cv.businesstime.team/health` returns `{"status":"ok"}` with a valid certificate.
- [ ] **Step 6:** On the real domain, repeat Task 5's browser checks 1–5, plus:
  - a Kimi K3 CV rewrite that takes more than 100s completes without an error
  - uploading a 2–5MB PDF CV works (Review Focus 1)
  - `curl -N` against `/api/chat` shows events arriving every few seconds, not all at the end. Interrupting it with Ctrl-C logs `outcome=abandoned` (`ssh root@<ip> dokku logs chat-ui`, Review Focus 3).
- [ ] **Step 7:** Ask the user to confirm the droplet is good before Task 10.

### Task 10: Remove App Platform (after the user confirms)

**Files:**
- Delete: `deploy/app.tf`
- Modify:
  - `deploy/outputs.tf` (remove `app_url`, `app_platform_id`; add `app_url = "https://${var.domain}"`)
  - `deploy/projects.tf` (remove the app's URN)
  - `deploy/variables.tf` (remove `app_instance_size`, `_app_source_repo`, `_app_source_branch`; `region` is used only by App Platform, so remove it and its tfvars line)
  - `deploy/terraform.tfvars`
  - `README.md` (remove the App Platform wording)
  - `chat-ui/Dockerfile` (comments that mention App Platform)

- [ ] **Step 1: Make the removals.**
- [ ] **Step 2: Check the plan.**
Run: `cd deploy && terraform validate && source env.sh && terraform plan`
Expected: the plan destroys only `digitalocean_app.chat_ui` and updates the project resources. The droplet is unchanged.
- [ ] **Step 3: Commit**

```bash
git add -A deploy/ README.md chat-ui/Dockerfile
git commit -m "feat: Remove the App Platform app now the Dokku droplet serves the chat UI"
```
