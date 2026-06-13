# Run the Chat UI Locally with Docker Compose — Design

**Date:** 2026-06-13
**Status:** Approved (pending spec review)

## Problem

We want to run the chat UI on a local machine for active development, without
deploying to App Platform on every change. The open question was whether the
DigitalOcean services (knowledge base, agent, guardrails) can run locally, or
whether we connect to the remote services without making them public.

## Key finding

The DO-managed services **cannot run locally**. The knowledge base, its
OpenSearch vector store, the managed GenAI agent, the guardrails, and serverless
inference are all DigitalOcean platform services with no local emulator.

However, **nothing needs to be exposed either**. The chat UI (`chat-ui/main.py`)
is a thin proxy: at startup it calls `api.digitalocean.com` to discover the
agent's deployment URL and mint an API key, then `/api/chat` forwards messages to
the agent's OpenAI-compatible endpoint over HTTPS, authenticated by that Bearer
key. The agent endpoint is internet-reachable but **gated by the secret API
key** — it is not open to anyone.

So the correct model is: **run the chat UI locally and let it connect outbound to
the remote managed agent.** No inbound tunneling, no exposing DO services.

```
localhost:8080  ──>  chat-ui container (local)
                          │  (outbound HTTPS, Bearer API key)
                          ▼
                  Remote managed agent endpoint  ──> KB / guardrails / inference (DO)
```

## Decisions

- **Workflow:** active development — mount source as a volume and run
  `uvicorn --reload` so edits to `main.py` / `static/index.html` reload without a
  rebuild.
- **API key handling:** reuse a pre-minted key via env vars rather than minting a
  new one on every startup (the current code does the latter, leaving an orphaned
  "chat-ui" key on the agent per restart).
- **`AGENT_ENDPOINT`:** hard-code the real value into the committed
  `.env.example` (it is gated by the API key, so it is not a secret).

## Changes

### 1. `chat-ui/main.py` — make discovery optional and overridable

Add two optional env vars, preserving today's App Platform behaviour:

- `AGENT_ENDPOINT` (optional): if set, use it directly; otherwise discover via the
  DO API as today.
- `AGENT_API_KEY` (optional): if set, use it directly; otherwise mint one via the
  DO API as today.
- If **both** are set, `_discover_agent()` is skipped entirely — no DO API call,
  no orphaned keys, and `DO_API_TOKEN` / `AGENT_UUID` are not required locally.
- In production, App Platform sets neither override, so behaviour is unchanged.

This requires loosening the module-level `os.environ["AGENT_UUID"]` and
`os.environ["DO_API_TOKEN"]` reads (which currently hard-fail with `KeyError` if
absent) so the override path can run without them. Startup must fail fast with a
clear message that names the missing variables when neither the override pair nor
the discovery inputs are fully present.

### 2. `docker-compose.yml` (repo root) — local dev service

- One `chat-ui` service built from `./chat-ui/Dockerfile`.
- Port mapping `8080:8080`.
- Volume mount `./chat-ui:/app` for live editing.
- `env_file: ./.env` to supply the local-dev variables.
- Override `command` to
  `uvicorn main:app --host 0.0.0.0 --port 8080 --reload`.

The dev `command` lives in compose, **not** the Dockerfile, so the shipped image
stays exactly as App Platform builds it.

### 3. `.env.example` (committed) — pre-filled endpoint, placeholder key

```
# The remote managed agent's chat endpoint. Pre-filled; only change if the
# agent is recreated with a new deployment URL.
AGENT_ENDPOINT=https://npqf2dhsif66dkb2fjtorw2u.agents.do-ai.run/api/v1/chat/completions

# An API key you mint once for the agent (see README). Reused across restarts.
AGENT_API_KEY=

# Display name shown in the UI header.
AGENT_NAME=Evenbreak CV Assistant
```

`.env` is already gitignored. Workflow: copy `.env.example` → `.env`, fill in
`AGENT_API_KEY`.

### 4. `deploy/outputs.tf` — add `agent_endpoint` output

Add an `agent_endpoint` output
(`digitalocean_gradientai_agent.rag_agent.deployment[0].url` + `/api/v1/chat/completions`)
so the endpoint can be retrieved with `terraform output agent_endpoint` if the
agent is ever recreated and the `.env.example` value goes stale.

### 5. README — clear "Local development with Docker" section

Replace the current bare-`uvicorn` snippet (lines ~264–275) with explicit,
copy-pasteable steps:

1. `cp .env.example .env`
2. **Mint an agent API key** (one time). Document both routes:
   - DO console → GenAI Platform → Agents → the agent → API Keys → create key →
     copy the secret; **or**
   - `curl` against the API:
     ```bash
     curl -s -X POST \
       -H "Authorization: Bearer $DO_API_TOKEN" \
       -H "Content-Type: application/json" \
       -d '{"name":"local-dev"}' \
       "https://api.digitalocean.com/v2/gen-ai/agents/<AGENT_UUID>/api_keys" \
       | python3 -c "import json,sys; print(json.load(sys.stdin)['api_key_info']['secret_key'])"
     ```
   Paste the secret into `.env` as `AGENT_API_KEY`.
3. (Only if the agent was recreated) refresh `AGENT_ENDPOINT` with
   `terraform output agent_endpoint`.
4. `docker compose up`
5. Open `http://localhost:8080`.

Note that the key is reused across restarts and should be revoked in the console
when no longer needed.

## Error handling

- Startup validation: if neither the override pair (`AGENT_ENDPOINT` +
  `AGENT_API_KEY`) nor the discovery inputs (`AGENT_UUID` + `DO_API_TOKEN`) are
  fully present, raise a clear error naming the missing variables instead of a raw
  `KeyError`.

## Testing / verification

- `docker compose up`, open `localhost:8080`, send a message, confirm a grounded
  answer returns from the remote agent.
- Edit `static/index.html` and confirm `--reload` picks up the change.
- The proxy logic is otherwise unchanged; no new automated tests in scope.

## Out of scope

- Running any DO service locally (not possible).
- Production-parity compose profile (we chose active-development mode only).
- Automatic API-key cleanup/rotation.
