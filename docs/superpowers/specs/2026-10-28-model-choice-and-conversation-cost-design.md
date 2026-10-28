# Model choice and conversation cost — design

Date: 2026-10-28

## Intent

The chat UI answers every message with one fixed model (`openai-gpt-oss-20b`,
set by `CHAT_MODEL`). We want to settle on the model to use long-term, so a
select group of job seekers will trial the app with a choice of ten models.

Two things come out of the trial:

- **Cost per conversation, per model**, read by the team from the app's logs.
- **User feedback on quality and cost.** The running cost is shown to users on
  purpose, so they can say whether a conversation felt worth what it cost.

This is temporary. Once a model is chosen the picker and cost display come out,
so the change should be easy to remove.

## Goals

- A model picker on the landing page; the chosen model answers the conversation.
- The choice locks once a conversation starts, so every conversation's cost
  belongs to exactly one model.
- A running total cost for the whole conversation, shown to the user.
- One log line per turn with the cost breakdown, never any message text.

## Non-goals

- Changing the guardrail model, query-rewrite model or embedding model. They
  stay as they are whatever the user picks (see "Fixed models" below).
- Tuning prompts per model. Everyone gets the same system prompt.
- Storing conversations or building a reporting dashboard. Logs are enough.
- Collecting feedback in the app. Feedback is gathered outside it.

## Anti-goals

- Logging message text, CV text or anything that identifies the user beyond the
  existing session id.
- A user being able to switch model mid-conversation, or change the displayed
  total, by editing the page or the request.
- Weakening the guardrails: the checks must run exactly as now for every model.

## Models

Shown in the dropdown as `<optgroup>`s by price tier and open/closed weights, in
this order. gpt-oss-20b is the default selection. Prices are USD per million
tokens from DigitalOcean's pricing page
(https://docs.digitalocean.com/products/gradient-ai-platform/details/pricing/),
checked 2026-10-28.

| Group              | Model             | Input | Output |
|--------------------|-------------------|-------|--------|
| Budget · open      | gpt-oss-20b       | 0.05  | 0.45   |
| Budget · open      | DeepSeek V4 Flash | 0.14  | 0.28   |
| Budget · closed    | Claude Haiku 5.5  | 0.10* | 0.50*  |
| Budget · closed    | GPT-6 Luna        | 0.10  | 0.50   |
| Mid-range · open   | Qwen 3.5 397B     | 0.55  | 3.50   |
| Premium · open     | DeepSeek V4 Pro   | 1.74  | 3.48   |
| Premium · open     | GLM-5.3           | 1.40  | 4.40   |
| Premium · open     | Kimi K3           | 3.00  | 15.00  |
| Premium · closed   | Claude Sonnet 5.5 | 2.00  | 10.00  |
| Premium · closed   | GPT-6 Sol         | 2.00  | 10.00  |

\* Haiku 5.5 is 0.50 / 2.50 above 100K prompt tokens. GPT-6 Luna and Sol list
prices for prompts up to 272K tokens. No conversation here should approach
either limit; the builder may price only the lower band (my call).

The dropdown shows names and group labels only, not prices (my call: the
group label already signals tier).

Fixed models, priced in the same table:

| Use                                   | Model                 | Price               |
|---------------------------------------|-----------------------|---------------------|
| Guardrail checks, query rewriting     | gpt-oss-20b           | 0.05 / 0.45         |
| Query embeddings                      | Qwen3 Embedding 0.6B  | 0.04 input only     |

**To verify before building:**

- The exact API model IDs, from `GET {INFERENCE_BASE_URL}/models` with the
  model access key. The pricing page only gives display names.
- Whether Kimi K3 is open weights. If it isn't, move it to "Premium · closed".
- "Qwen" means Qwen 3.5 397B, not Qwen3.8-Max (which is closed weights).

## Why the other calls stay on gpt-oss-20b

Guardrails are a narrow yes/no classification already tuned against
`evals/guardrails_eval.py` for gpt-oss-20b. Remaining issues (one intermittent
missed block, occasional empty replies now covered by a retry) don't justify
retuning per model, and running them on premium models would multiply the cost
of every message. Embeddings must match the prebuilt index, so changing them
means re-indexing, and they don't affect what users experience.

## User experience

Layout A from the mockups (the user chose it):

- **Landing page:** above the two route cards ("Upload your CV" and
  "Questions & answers"), a labelled select: "AI model for this conversation".
- **In conversation:** a thin strip under the site header showing
  `Model: <name>` with a lock indicator on the left, and
  `Conversation cost so far: $0.0412` on the right.
- Cost is shown in US dollars to four decimal places, updated after every
  response, including blocked messages and failed turns (they still cost
  something).
- **Locking:** the conversation starts when the user uploads a CV or clicks
  "Ask a question". From then on the model can't change. "Back" returns to the
  landing page, which starts a new conversation and unlocks the picker. The
  picker keeps its last value.

## Cost

The total covers every model call made for the conversation:

- the CV check at upload (`check_cv`, which may make several calls),
- per message: the message check (`check_message`), query rewriting
  (`rewrite_queries`), query embedding (`embed`) and the reply (`chat` with the
  chosen model),
- the guardrail retry, when it happens.

Cost of a call = input tokens × input price + output tokens × output price,
using the `usage` block every OpenAI-compatible response returns
(`prompt_tokens`, `completion_tokens`; embeddings report `prompt_tokens` or
`total_tokens`). If a response has no `usage`, log a warning and count it as
zero, never failing the turn. Reasoning tokens are billed as output and are
already part of `completion_tokens`.

Prices live in one table in the app (a Python dict keyed by model ID), not
fetched from DigitalOcean.

## Server-side rules (decided)

- The server enforces the lock. The model is chosen when the conversation
  starts (in the upload request, or in the first chat message for Q&A) and
  bound to the conversation so a later request can't change it. Requests that
  name an unknown model are rejected with a 400.
- The server works out the running total. The browser only displays it.
- Both the model and the total must be tamper-proof. The obvious route is to
  include them in what `history_signing` already signs, alongside the history
  and session id. The builder chooses the mechanism.
- Blocked and failed turns currently return the previous `history_token`
  unchanged. They will now add cost, so they need to return an updated total
  and token without adding the turn to the history.

## Edge cases (my calls, settled after the builder check)

- **Rejected CV uploads** are not conversations. Their guardrail cost is logged
  on its own line (`kind=upload outcome=rejected`, the chosen model, no
  session id) and isn't shown to the user or carried into a later upload. The
  conversation, and its total, start at a successful upload.
- **Per-model request settings are allowed.** This is not per-model prompting.
  The model list may carry settings per model (e.g. omit `top_p`, use
  `max_completion_tokens`, a larger output budget for reasoning models) so each
  model gets a fair run. Before building the rest, call every model once with
  the real request shape to confirm it is available on the key and returns a
  non-empty reply. **If a model is missing or can't be made to work, stop and
  ask the user** whether to drop or replace it.
- **Token expiry ends the conversation.** When the history token is rejected
  as expired, or the CV session is lost on restart, the browser returns to the
  landing page and the user starts a new conversation (picker unlocked, total
  reset). This changes today's expiry path, which keeps the CV session. It
  keeps each conversation on one model with an honest total, without
  server-side state for Q&A.
- **Replaying an older token is accepted.** Signing stops edits but not
  resending an older valid token, which would roll back the displayed total.
  That matches today's history signing and isn't worth server-side state for a
  trial. The per-turn log lines are the source of truth for the team's
  comparison; the on-screen total is for users' impressions.

## Logging

One `INFO` line per turn (and one for the upload check), for example:

```
Turn cost: session=<id> model=<id> kind=chat|upload outcome=answered|blocked|error
  chat_in=… chat_out=… guard_in=… guard_out=… embed_in=… turn_usd=0.001234 total_usd=0.004567
```

Q&A conversations currently have no session id. They need one so turns can be
grouped into conversations (a fresh UUID when the Q&A conversation starts).
Never log message text, CV text or the filename.

## Deployment

- The DigitalOcean model access key (`model_access_key` in `deploy/variables.tf`,
  description says it is scoped to gpt-oss-20b and Qwen3 Embedding) must be
  widened to cover all ten models. Update the variable description to match.
- `CHAT_MODEL` stays as the fixed model for guardrails and query rewriting, and
  as the picker's default.

## Removal

Keep the picker, cost strip, price table and model list in clearly separable
pieces (one Python module for the model list and prices; one block of markup
and script in `index.html`), so removing the trial means deleting them and
setting `CHAT_MODEL` to the winner.

## Left to the builder

- How the model and total are bound to the conversation and signed.
- How usage is collected from `chat`/`embed` and threaded through `guardrails`
  and `prompting` (return values, an accumulator object, etc.).
- Exact log format, as long as it has the fields above.
- Styling of the select and strip, within the existing `styles.css` conventions.
- Tests: follow the existing `tests/` style. At minimum cover cost arithmetic,
  rejecting an unknown model, the lock (a changed model after the start is
  rejected or ignored), tampered totals being rejected, and blocked turns
  adding cost.
