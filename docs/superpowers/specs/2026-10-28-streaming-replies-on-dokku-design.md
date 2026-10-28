# Streaming replies on Dokku — design

Date: 2026-10-28

## Intent

In the model trial, several models are slow, and some reply with nothing or
with a cut-off answer. Measured on 2026-10-28 with a real request (system
prompt, a test CV, "rewrite my whole CV and explain each change"):

- **Empty and cut-off replies come from the token limit.** Qwen 3.5, GLM-5.3
  and Kimi K3 think before answering, and thinking counts towards
  `max_tokens` (2048 today). They used all of it thinking and returned no
  content (`finish_reason=length`). The app doesn't check `finish_reason`, so
  an empty or cut-off reply is shown, added to the history and charged.
- **Without a limit they all finish.** Qwen used 3,247 tokens, GLM 7,557 and
  Kimi 6,066, with complete answers.
- **They're slow because of how much they generate, not the network.** With
  streaming, the first token arrives within 1s for every model. The answer
  itself doesn't start until 100–117s, after the thinking, and a whole turn
  takes up to about 260s. Output speed also varies a lot between runs.
- **App Platform can't carry this.** It has a hard 100s limit per request
  (according to DigitalOcean staff on the community forums) and reportedly
  buffers streamed responses at its edge until they finish.
- **The model calls can't move to the UK.** DigitalOcean's regions API lists
  only ric1, atl1 and tor1 for serverless inference. Network distance isn't
  the bottleneck anyway.

So we let the models think as much as they need, stream every reply to the
browser so people see progress within a second or two, and move hosting from
App Platform to a single droplet running Dokku, where we control the proxy's
timeouts and buffering. This is an MVP: simple and good enough beats
bulletproof.

## Goals

- Every trial model returns a complete answer to realistic requests.
- A status line appears as soon as a message is sent, its timer ticks every
  second, and its text changes every 5s until the answer starts.
- The answer streams in as it is generated.
- Hosting stays as low-maintenance as App Platform: automatic deploys on push
  to main, no manual patching, secrets set once.

## Non-goals

- Turning thinking off or down for any reply model. Models think freely when
  answering; the trial compares them as they are. (The status summariser
  below is not a reply and runs on low effort.)
- Showing the model's raw thinking text to users.
- Multiple app instances, high availability or zero data loss across
  redeploys. CV sessions and in-flight replies are lost on redeploy, as now.
- Moving inference off DigitalOcean (OpenRouter was considered; it doesn't fix
  the timeout, and it's a separate decision).
- Storing conversations server-side or keeping replies running after the
  browser disconnects.

## Anti-goals

- An empty or cut-off reply shown to the user as if it were a real answer.
- Weakening the guardrails: the message check still runs, and must pass,
  before the reply model is called.
- Anything a user could use to read another user's reply, or to tamper with
  history or the running cost. The browser-held state stays signed exactly as
  today.
- Giving the browser any model credential.
- Status text that misleads ("nearly done") or carries content from the
  thinking verbatim, such as links or quoted instructions.

## Hosting: one droplet running Dokku

Decided with the user:

- **Server:** one droplet in LON1 running Dokku (a small self-hosted
  Heroku-style platform), created by Terraform along with a firewall that
  allows only 22, 80 and 443 in. 2GB RAM, because Dokku builds the image on
  the droplet.
- **Domain:** `evenbreak-cv.businesstime.team`. Its DNS isn't hosted on
  DigitalOcean, so the user adds the A record by hand, once, pointing at the
  reserved IP (below). HTTPS comes from Dokku's Let's Encrypt plugin, with automatic
  renewal.
- **Deploys:** automatic on push to main, as today. A GitHub Actions workflow
  pushes the repo to Dokku with Dokku's official action, and Dokku builds
  `chat-ui/Dockerfile` from the repo root and switches over once the health
  check passes. The existing Terraform workflow stays for infrastructure
  changes.
- **Secrets:** set on the server with `dokku config:set` (`MODEL_ACCESS_KEY`,
  `HISTORY_SIGNING_KEY`). `MODEL_ACCESS_KEY` is also passed as a Docker build
  argument, because the image build embeds the knowledge base.
- **Proxy:** Dokku's nginx for the app is set to `proxy-buffering off` and
  `proxy-read-timeout` of about 600s.
- **Patching:** Ubuntu unattended security upgrades, with an automatic reboot
  at night when an update needs one. Dokku restarts the app after a reboot or
  crash.
- **Changeover:** the App Platform app keeps running until the droplet is
  confirmed working on the new domain. Then Terraform removes the app.

### Server setup is all in Terraform

Decided with the user: Terraform covers as much of the server setup as
possible. Setting up a new server should need only config and secrets, with
no manual steps on the server.

- **First-boot setup:** the droplet's `user_data` is a cloud-init script,
  rendered by Terraform from a template in the repo. On first boot it:
  - installs Dokku (a pinned version) and its plugins (at least
    `dokku-letsencrypt`)
  - creates the app and sets its domain
  - sets its config and secrets, and the build argument
  - applies the nginx settings above
  - adds the deploy public key
  - sets up unattended upgrades
  - enables Let's Encrypt with automatic renewal
- **Inputs:** everything is Terraform variables or Terraform-generated:
  - the domain
  - the Let's Encrypt contact email
  - `MODEL_ACCESS_KEY` (already a variable)
  - `HISTORY_SIGNING_KEY` (stays the existing `random_password`)
  - the deploy SSH key pair (generated by Terraform with `tls_private_key`;
    the private key is a sensitive output that goes into the GitHub Actions
    secret once)
  - admin SSH keys for the droplet
- **Rebuilding is normal:** the server is disposable. Changing the setup
  (including rotating a secret) means Terraform replaces the droplet and the
  next push to main deploys onto it. CV sessions are lost, as on any
  redeploy. A **reserved IP** stays attached across rebuilds, so DNS never
  changes.
- **A rebuilt server is deployed straight away** (our call). The Terraform
  workflow, after an apply that replaced the droplet, waits for cloud-init to
  finish (`cloud-init status --wait` over SSH) and then runs the deploy, so
  the site is never left empty until the next push. The Terraform and deploy
  workflows share one concurrency group, so a push touching `deploy/` never
  deploys to a droplet that's being replaced or still setting up.
- **Stable SSH host key** (our call): Terraform generates the droplet's SSH
  host key and installs it through cloud-init, so the host key is the same
  across rebuilds. Its public half is a Terraform output that goes into the
  deploy workflow's `known_hosts` once.
- **Let's Encrypt rate limits:** frequent rebuilds re-issue the same
  certificate, and Let's Encrypt allows only a few duplicates a week. That's
  fine at MVP rebuild rates.
- **Let's Encrypt timing:** issuing a certificate needs DNS already pointing
  at the reserved IP, and possibly a deployed app. The setup must cope with
  both without a manual step. For example, it can retry until DNS resolves,
  or the deploy workflow can enable Let's Encrypt idempotently after each
  deploy. The builder confirms which the plugin needs.
- **Secrets end up in the droplet's metadata and in Terraform state.** State
  already holds them today (in the Spaces backend), so this is accepted for
  the MVP.

## Replies: stream, with no practical limit

- **Limit:** the reply's `max_tokens` goes from 2048 to 32,768. That's far
  above what any model used (7,557 at most) but still caps a runaway turn;
  Kimi output at $15 per million tokens makes 32k about $0.50.
- **Streaming from the provider:** `inference.chat` gains a streaming variant
  that reads DigitalOcean's stream (`stream: true`,
  `stream_options: {include_usage: true}`). It yields thinking text
  (`reasoning_content`) and answer text (`content`) separately, and finally
  the usage and `finish_reason`.
- **Timeout:** the 120s limit on the whole request becomes a limit on silence:
  if no chunk arrives for about 60s, the call fails.
- **Cut-off replies:** a `finish_reason` other than `stop`, or a stream that
  ends with no answer text, is a failure, the same as an HTTP error.
- **Background calls:** the message check (`guardrails.py`) and query rewrite
  (`prompting.py`) stay non-streaming, but run on gpt-oss-20b, which also
  thinks, so their limits go up (to about 4,096). Their reasoning effort stays
  as it is, so the guardrail behaviour measured by `evals/guardrails_eval.py`
  doesn't change (our call). They should also treat a
  cut-off reply as a failure, which they already handle: the check fails
  closed, the rewrite falls back to the raw message.

## The chat request becomes a stream

`POST /api/chat` takes the same request body as today and verifies it the
same way: a bad signature, a lost CV session or an unknown model still gets
the same 400 JSON response before any streaming starts. After that, the
response is a stream of newline-delimited JSON events, one per line:

- `status` — `{text}`: a new status line while the model thinks.
- `answer` — `{text}`: the next piece of the answer.
- `done` — the final event for a turn that worked or was blocked: the full
  content, `blocked`, `sources`, and the new signed conversation state
  (history, `history_token`, session id, model, total), as the JSON response
  carries today.
- `error` — the final event for a turn that failed: the error message and the
  new signed state, which includes the failed turn's cost but not the turn
  itself, as today.

A blocked message produces no `status` or `answer` events, just `done` with
the canned reply. The check and retrieval still run together before the reply
model is called.

If the browser disconnects (tab closed, or the user starts a new
conversation), the server cancels the model call and any summary calls. The
cost so far can't reach the browser, so it's only logged, with
`outcome=abandoned`.

The evals (`evals/compare.py`) call `/api/chat`, so they need updating to read
the stream (only the `done`/`error` event matters to them), and their 180s
timeout has to go up, since turns take up to about 260s.

## What the user sees while the model thinks

Decided with the user, from mockups (option A):

- **The status line:** the assistant's message shows one line with a pulsing
  dot and a running timer ("Matching your volunteering to coordinator skills…
  · 42s"). Every 5 seconds the line is replaced (decided
  with the user, to give the summariser plenty of time); the timer ticks
  every second.
- **Before the server has anything to say** (our call): while the message
  check and retrieval run, the browser shows the status line straight away
  ("Reading your message…") and replaces it every 5s from its own short list
  of set phrases, until the first server event arrives. This replaces today's
  bouncing dots. The timer, and "Thought for…", count from when the message
  was sent. The server never sends `status` events before the check has
  passed, so a blocked message still produces only `done`.
- **Models without visible thinking** keep the browser's status line until
  the first `answer` event.
- **The line always changes,** even when the model has produced no new
  thinking. The summariser is then asked for a different wording of where
  things stand.
- **Once the answer starts,** the status line becomes "Thought for 1m 41s"
  in small muted text, and the answer streams in below it.
- **Errors:** if the turn fails at any point, including after some of the
  answer has arrived, the partial answer is removed and an error is shown
  saying to try again, in plain words rather than the raw exception text shown
  today. The user's message goes back into the input box so they can resend
  it, as already happens when the check fails.
- **Starting a new conversation mid-reply** aborts the request in the browser
  (today it only ignores the late reply), so the server sees the disconnect
  and cancels the model call.

### Summarising the thinking

- **The summariser:** every 5s while the reply model is thinking, the server
  sends the thinking produced since the last update, plus the previous status
  line, to gpt-oss-20b with `reasoning_effort: "low"` and a strict
  instruction. (Measured 2026-10-28: about 0.9–1.1s per call at low effort,
  against 1.8–2.7s at the default, which leaves less headroom on a slow
  run.) It asks for one short,
  friendly, present-tense line about what's happening, worded differently
  from the previous one, and no quoting of the CV or of instructions.
- **No overlapping calls:** each summary call is started as soon as the
  previous update has been sent, and has until the next 5s update to come
  back. One that hasn't is cancelled, and the fallback below is used.
- **Cleaning the output:** before it's sent, the line is cut to a set length
  (about 80 characters) and links and markup are removed.
- **Fallback:** if a summary call fails, comes back empty or takes longer than
  the 5s cycle, the server sends the next of a short list of set phrases
  ("Still working through your CV…"). These must never claim progress the app
  doesn't know about. The timer is drawn by the browser and keeps running
  regardless. A summary failure never fails the turn.
- **Cost:** summary calls are recorded with their own purpose (`status`), so
  they appear in the turn's cost log line and are included in the user's
  running cost (decided with the user). At about 450 tokens in (5s of new thinking
  plus the instruction) and 25–70 out per call, that's roughly $0.001 for a
  turn with 100s of thinking (20 calls),
  against $0.01–0.09 for the reply itself.

## Cost tracking

The ledger works as now (`trial.recording` and `trial.purpose`). Streamed
usage arrives in the final chunk, and `record_usage` takes it from there.
Summary calls made from a background task must still reach the turn's ledger:
a task started inside the `recording` context inherits it.

`cost_log_line` gains `status_in`/`status_out` fields.

## Left to the builder

- The names and exact shape of the streaming function in `inference.py`, and
  how the summariser loop is structured.
- The wording of the summariser prompt and the set fallback phrases, within
  the rules above.
- Markup and styling of the status line, using the existing tokens in
  `styles.css`. It should be announced politely to screen readers without
  reading out every 5s change (for example, one announcement when thinking
  starts and one when the answer starts).
- The Dokku health check and the exact cloud-init steps.
- Tests: the existing suite covers the pipeline with a fake inference layer;
  extend it for streaming, cut-off replies, the summariser fallback and
  disconnects.
