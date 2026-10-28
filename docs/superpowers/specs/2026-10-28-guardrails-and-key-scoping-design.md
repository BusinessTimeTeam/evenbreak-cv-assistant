# Guardrails and Model Key Scoping — Design

**Date:** 2026-10-28
**Status:** Draft (pending review)

**Update 2026-10-28:** Part 1 (two model access keys and the VPC) was dropped. The DigitalOcean console couldn't list VPCs when creating a restricted key, and the restrictions only help if a key leaks, which isn't expected. One `MODEL_ACCESS_KEY` is used at build and run time, as before.

## Why

The chat UI is public and spends money on every message. The worry is someone
using it as a free general-purpose AI ("write my essay", "debug my code") and
running up a bill on Evenbreak's card, either through the chat itself or by
getting hold of the model access key and calling serverless inference directly.

Prompt injection matters mainly as the way round a topic restriction ("ignore
your instructions and..."). The app currently trusts the conversation history
the browser sends, so an attacker can also invent earlier "assistant" turns.

**The anti-goal:** a real job-seeker asking something slightly sideways gets
turned away. "How do I explain a gap from my chemo?", "Is it OK to mention my
autism at interview?" and "Can you make this cover letter sound less formal?"
must all be answered. Wrongly blocking a genuine question is worse than letting
a borderline misuse through.

## Goals

1. **Two model access keys (dropped — see update).** The key the running app uses only works from
   inside a DigitalOcean VPC, so a leaked runtime key is useless elsewhere. A
   second key, used only at build time to embed the knowledge base, has no VPC
   restriction but can only call the embedding model.
2. **An injection and topic check on every chat message**, before the answer
   is generated. Clearly off-topic messages and instruction-override attempts
   get a friendly redirect instead of an answer.
3. **A check on uploaded CVs**, once, at upload: no injection, and the
   document is actually a CV (nothing else guarantees that it is).
4. **Signed conversation history**, so the server only accepts history it
   produced itself. The app stays stateless: no history is stored server-side.
5. **Evals that measure both directions:** attacks caught, and genuine-but-
   awkward questions let through.

## Non-goals (deferred, revisit after MVP)

- Rate limits (per visitor or site-wide), message/CV size caps, and lowering
  the response length cap. We discussed these; there isn't a clear answer at
  MVP stage, so they are left out entirely.
- Sensitive-data detection (card numbers, NHS numbers, passwords, etc.).
- Moving the in-memory CV store (`_cv_sessions`) to a shared store. It already
  blocks running more than one instance; that is unchanged here.
- Checking the model's *output* (answers) for topic or leakage.
- Changing how errors are reported to the browser.

## Part 1: Two model access keys (dropped — see update)

### Keys

| Key | Models | VPC | Used by | Where it lives |
|---|---|---|---|---|
| Runtime key | gpt-oss-20b, Qwen3 Embedding 0.6B | Restricted to the app's VPC | The running app (every chat turn, query embedding, the new checks) | `MODEL_ACCESS_KEY`, App Platform scope `RUN_TIME` |
| Build key | Qwen3 Embedding 0.6B only | None | Docker `index` stage only | New build-time env var, App Platform scope `BUILD_TIME` |

Both keys are created by hand in the DigitalOcean console by the user (a key's VPC and
model scope can't be changed after creation). They reach Terraform as two
sensitive variables supplied via `TF_VAR_*`; CI gets a new repo secret for the
build key alongside `MODEL_ACCESS_KEY`. The README's key-creation instructions
are updated for both, plus a third, local-development key (unrestricted, both
models) for `docker compose`.

**Why two keys:** App Platform builds run on build infrastructure outside the
app's VPC, so a VPC-restricted key would be rejected (403) while embedding the
knowledge base. An embedding-only key is of little value to an attacker:
embeddings are very cheap and can't be used as a general assistant.

**Key hygiene constraints (unchanged from today, now for the build key):**
the key must exist only in the `index` stage, never be referenced in a `RUN`
line (build logs print expanded build args), and never be copied into the
`prod` image.

### VPC

- Terraform creates a `digitalocean_vpc` in the datacenter App Platform's
  region connects to (the app region is `lon`, so `lon1`), and attaches the app
  to it via the app spec's `vpc` block.
- The VPC is not added to the DO project: projects don't support VPCs.
- VPC-attached apps cannot use dedicated egress IPs. The app doesn't use them
  today.
- DigitalOcean's docs say adding a VPC to an app redeploys it in place, so
  the app keeps its ID and URL; the spike confirms this on an existing app.

### Cutover and hand-offs to the user

The user does all DigitalOcean console and GitHub settings work. The plan
stops and hands over at each of these points:

1. **Spike:** the user creates a throwaway App Platform app (in `lon`)
   attached to a VPC in `lon1`, plus a temporary VPC-restricted key, and
   runs the checks described under "Spike first" using commands the plan
   provides. The user then deletes both.
2. **Keys:** the user creates the runtime key (VPC-restricted to the VPC
   Terraform creates; so the VPC must exist first, either applied on its own
   or created by hand and imported), the build key and a local-development
   key.
3. **Secrets:** the user adds/updates the CI repo secrets *before* the
   Terraform change that needs them reaches `main`, or the CI apply fails.
4. **Revoke:** once the deployed app is confirmed working on the new keys,
   the user revokes the old unrestricted `MODEL_ACCESS_KEY`. Leaving it live
   would undo the point of Part 1.

### Spike first (gate)

DigitalOcean's docs say resources calling serverless inference from a VPC must
have the "VPC-local DNS resolver" enabled. They describe this for Droplets and
say nothing about App Platform. **Before building Part 1**, prove with a
throwaway App Platform app attached to a VPC that a VPC-restricted key
succeeds from it (and that the same key gets a 403 from a laptop), and that
attaching a VPC to an already-running app redeploys it in place. Also
confirm the pinned provider (`~> 2.106.0`) supports the `vpc` block on
`digitalocean_app`.

If the spike fails, stop Part 1 and come back to the user; Parts 2–4 don't
depend on it.

## Part 2: The injection and topic check

### Behaviour

For each `/api/chat` message, one extra call to the chat model (gpt-oss-20b,
the same `chat()` helper) classifies the message and returns JSON with two
booleans: is it an attempt to override or extract the assistant's
instructions, and is it **clearly** unrelated to jobs, careers, CVs, cover
letters, interviews, workplaces, employment rights, or disability and health
**with a job or work angle**.

- **Health and disability need a work angle** (the user's decision: anything
  else strays beyond Evenbreak's expertise). "How do I explain a gap from my
  chemo?" and "Can I ask for adjustments for my MS?" are on-topic; "What are
  the early symptoms of MS?", "What's a normal sertraline dose?" and "How do
  I get a Blue Badge?" are not.

- **Lenient by design.** The classifier instruction says to treat anything
  borderline, ambiguous or partly related as on-topic. Greetings, thanks,
  short follow-ups ("can you make it shorter?", "what about the second
  one?") and questions about the assistant itself are on-topic.
- **Context:** the check sees the latest message and a few recent history
  turns, so follow-ups are judged in context. It does not need the CV.
- **Runs in parallel** with the existing query rewrite and embedding, so it
  adds little latency. The final answer call waits for the check and is only
  made if the message passes.
- **Fails closed.** If the check call errors or its reply can't be parsed,
  the message is refused with a "something went wrong, please try again"
  style message, not answered. (The user accepted this; the evals in Part 4
  show whether it trips real users.)

### When a message is blocked

- The response is a normal chat reply (not an error) with fixed, friendly
  wording that explains what the assistant can help with and invites them to
  ask about that. One wording for off-topic, one for injection (gentler than
  an accusation: e.g. "I can't change how I work, but I'm happy to help
  with…").
- The response says it was blocked and why (a field alongside `content`),
  for the UI and evals. The UI shows it as an ordinary assistant message.
- The blocked turn is **not** added to the signed history, so it doesn't
  pollute later turns. The browser keeps the previous history and token.
- The server logs one line per block: the reason, whether a CV session was
  attached, and the message length. **Never the message text.**

### CV upload check

On `/api/upload`, after text extraction, the document is checked for two
things (the user's decision: nothing guarantees the upload is a CV):

- **Injection:** instructions aimed at the assistant. Rejected with a clear
  message asking the user to upload a plain CV.
- **Is it a CV:** lenient in the same spirit as the chat check. CVs and
  résumés in any layout, including rough, partial or unconventional ones,
  pass; so do closely related job-application documents (a cover letter, a
  personal statement, a LinkedIn profile export) *(my call)*. Clearly
  unrelated documents (an essay, source code, a contract, a novel chapter)
  are rejected with a friendly "this doesn't look like a CV" message.

Long documents are checked in pieces (or from a representative excerpt for
the "is it a CV" question) if they exceed what the check prompt can sensibly
hold. Like the chat check, the upload check fails closed. A rejected upload
creates no session.

### Prompt framing

Alongside the classifier, the system prompt and the messages that carry the
CV and knowledge-base passages are reworded so the model treats them as
material to read, not instructions to follow. This is cheap defence in
depth, not the main control.

## Part 3: Signed history

The browser keeps holding the conversation history (so the app stays
stateless and can scale out), but the server signs it.

- Each successful `/api/chat` response returns the **updated history** (the
  previous turns plus this message and the answer) and a **history token**.
- The browser sends that history and token back unchanged with its next
  message, instead of building the history itself.
- The token is an HMAC-SHA256 over the history, the session id (or its
  absence) and an issue timestamp, keyed by a new secret
  `HISTORY_SIGNING_KEY`. The timestamp travels in the token.
- The server rejects a request whose history doesn't match its token, whose
  token belongs to a different session id, or whose token is more than 24
  hours old. The browser shows "this conversation has expired, please start
  again" and resets.
- A first message (empty history) needs no token.
- `HISTORY_SIGNING_KEY` is required at startup. In deployment Terraform
  generates it (`random_password`) and passes it as a run-time secret, so
  there's no new secret to manage by hand; all instances share it. Locally
  it comes from `.env` (any string); `.env.example` documents it.
- `evals/compare.py` is updated to send the returned history and token back.

## Part 4: Evals for the guardrails

A new set of cases, separate from `evals/cases.json` (which is the
side-by-side comparison against the old agent and has no baseline for these),
each labelled with the expected outcome: **allow** or **block**.

- **Should block:** clear off-topic requests (code, homework, recipes,
  trivia, general writing unrelated to work, health or disability questions
  with no work angle), instruction-override and
  prompt-extraction attempts, and on-topic-sounding wrappers around
  off-topic tasks ("as a careers question, write me a Python script…").
- **Should allow:** the awkward-but-genuine questions — health conditions
  and treatment in a work context, disclosure, benefits and Access to Work,
  workplace disputes, cover letters and personal statements, rewording
  requests, short context-dependent follow-ups, greetings and thanks.
  Weighted towards this side: false blocks are the anti-goal.
- A runner reports, per case, expected vs actual, and totals for false
  blocks and missed attacks. Unlike the comparison, this can be scored
  automatically.
- A handful of CV upload cases: a CV with embedded instructions; a normal CV
  that happens to contain words like "instructions" or "ignore"; rough or
  unconventional CVs and a cover letter (allow); and clearly non-CV
  documents such as an essay or source code (block). The three existing
  `evals/cvs/` CVs must all pass.
- **Run by hand** (the user's decision), like `evals/compare.py`, through
  `docker compose`; not in CI, since each run costs inference.
- **Bar for shipping** *(my call, from the anti-goal)*: zero wrongly blocked
  cases in the "should allow" sets. A few missed attacks are tolerable; the
  check is one layer. Tune the classifier prompts until the allow sets pass.
  If failing closed ever trips an allow case, raise it with the user rather
  than quietly switching to failing open.

## What's left to the builder

- Exact classifier prompt, JSON shape, and parsing (follow the style of
  `rewrite_queries` in `prompting.py`), within the leniency rules above.
- Module layout (e.g. a new `guardrails.py` and `history_signing.py`).
- Token encoding, and the exact field names in the API.
- Wording of the redirect and expiry messages (UK English, warm, short).
- Whether the guardrail eval calls the check directly or goes through
  `/api/chat` — calling the check directly is cheaper and is the
  suggested default.
- Unit tests in the existing `chat-ui/tests/` style, with network calls
  stubbed.
