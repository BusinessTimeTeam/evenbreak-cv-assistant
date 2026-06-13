# Local Chat UI with Docker Compose — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the chat UI locally in Docker Compose with live-reload, connecting outbound to the remote DO-managed agent via pre-set endpoint/key env vars.

**Architecture:** The chat UI is a thin FastAPI proxy. We make agent discovery optional: if `AGENT_ENDPOINT` and `AGENT_API_KEY` are both set, the app uses them directly and skips all DO API calls; otherwise it discovers them as today (unchanged App Platform behaviour). A root `docker-compose.yml` builds the existing Dockerfile, mounts the source for `--reload`, and reads a `.env` file.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, httpx, Docker Compose, Terraform (DigitalOcean provider).

---

## File Structure

- `chat-ui/main.py` — modify: make config tolerant of missing env, add `_resolve_config()` to choose override-vs-discovery and validate.
- `chat-ui/tests/test_config.py` — create: unit tests for `_resolve_config()`.
- `chat-ui/requirements-dev.txt` — create: dev-only deps (pytest).
- `docker-compose.yml` — create (repo root): local dev service.
- `.env.example` — create (repo root): committed template with real `AGENT_ENDPOINT`, placeholder `AGENT_API_KEY`.
- `deploy/outputs.tf` — modify: add `agent_endpoint` output.
- `README.md` — modify: replace the "Local development" section.

---

## Task 1: Make agent config optional and overridable in `main.py`

**Files:**
- Modify: `chat-ui/main.py:28-31` (module-level env reads) and the startup path
- Create: `chat-ui/tests/test_config.py`
- Create: `chat-ui/requirements-dev.txt`

- [ ] **Step 1: Create the dev requirements file**

Create `chat-ui/requirements-dev.txt`:

```
-r requirements.txt
pytest==8.3.3
```

- [ ] **Step 2: Write the failing test**

Create `chat-ui/tests/test_config.py`:

```python
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
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd chat-ui && pip install -r requirements-dev.txt && python -m pytest tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name '_resolve_config'` (and the module-level `os.environ["AGENT_UUID"]` may raise `KeyError` on import).

- [ ] **Step 4: Make module-level config tolerant and add `_resolve_config`**

In `chat-ui/main.py`, replace the module-level config block (currently lines 28-31):

```python
AGENT_UUID = os.environ["AGENT_UUID"]
DO_API_TOKEN = os.environ["DO_API_TOKEN"]
AGENT_NAME = os.environ.get("AGENT_NAME", "RAG Assistant")
DO_API_BASE = os.environ.get("DO_API_BASE", "https://api.digitalocean.com")
```

with:

```python
# Discovery inputs (required only when overrides below are not provided).
AGENT_UUID = os.environ.get("AGENT_UUID")
DO_API_TOKEN = os.environ.get("DO_API_TOKEN")
AGENT_NAME = os.environ.get("AGENT_NAME", "RAG Assistant")
DO_API_BASE = os.environ.get("DO_API_BASE", "https://api.digitalocean.com")

# Optional overrides for local development: when both are set, the app talks to
# the remote agent directly and skips all DO API discovery (so no DO_API_TOKEN
# or AGENT_UUID is needed locally, and no per-restart API key is minted).
AGENT_ENDPOINT = os.environ.get("AGENT_ENDPOINT")
AGENT_API_KEY = os.environ.get("AGENT_API_KEY")
```

Then **delete** the now-duplicate "Populated at startup" block (currently lines 33-35):

```python
# Populated at startup.
AGENT_ENDPOINT = None
AGENT_API_KEY = None
```

Add the resolver function (place it just above `def _discover_agent():`):

```python
def _resolve_config(endpoint, api_key, agent_uuid, do_token):
    """Decide whether startup needs DO API discovery.

    Returns False when both overrides (endpoint + api_key) are present, meaning
    discovery is skipped. Returns True when discovery is needed and its inputs
    are present. Raises RuntimeError naming the missing variables otherwise.
    """
    if endpoint and api_key:
        return False
    missing = [
        name
        for name, value in (("AGENT_UUID", agent_uuid), ("DO_API_TOKEN", do_token))
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Incomplete configuration. For local dev set AGENT_ENDPOINT and "
            "AGENT_API_KEY; otherwise set "
            f"{', '.join(missing)} so the agent can be discovered."
        )
    return True
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd chat-ui && python -m pytest tests/test_config.py -v`
Expected: PASS — 4 passed.

- [ ] **Step 6: Wire the resolver into startup**

In `chat-ui/main.py`, replace the startup handler (currently lines 80-82):

```python
@app.on_event("startup")
async def startup_event():
    _discover_agent()
```

with:

```python
@app.on_event("startup")
async def startup_event():
    if _resolve_config(AGENT_ENDPOINT, AGENT_API_KEY, AGENT_UUID, DO_API_TOKEN):
        _discover_agent()
    else:
        logger.info(
            "Using AGENT_ENDPOINT/AGENT_API_KEY overrides; skipping DO API discovery"
        )
```

- [ ] **Step 7: Verify the module imports without any env vars set**

Run: `cd chat-ui && env -u AGENT_UUID -u DO_API_TOKEN -u AGENT_ENDPOINT -u AGENT_API_KEY python -c "import main; print('import ok')"`
Expected: prints `import ok` with no exception.

- [ ] **Step 8: Commit**

```bash
git add chat-ui/main.py chat-ui/tests/test_config.py chat-ui/requirements-dev.txt
git commit -m "Make agent endpoint/key overridable for local dev"
```

---

## Task 2: Add `docker-compose.yml` and `.env.example`

**Files:**
- Create: `docker-compose.yml` (repo root)
- Create: `.env.example` (repo root)

- [ ] **Step 1: Create `.env.example`**

Create `.env.example` at the repo root:

```
# Local development configuration for the chat UI (docker compose).
# Copy this file to .env and fill in AGENT_API_KEY. See the README
# "Local development with Docker" section for how to mint a key.

# The remote managed agent's OpenAI-compatible chat endpoint. Pre-filled with
# the current deployment URL; only change it if the agent is recreated (run
# `terraform output agent_endpoint` from deploy/ to get the new value).
AGENT_ENDPOINT=https://npqf2dhsif66dkb2fjtorw2u.agents.do-ai.run/api/v1/chat/completions

# An API key you mint once for the agent and reuse across restarts.
AGENT_API_KEY=

# Display name shown in the chat UI header.
AGENT_NAME=Evenbreak CV Assistant
```

- [ ] **Step 2: Create `docker-compose.yml`**

Create `docker-compose.yml` at the repo root:

```yaml
# Local development for the chat UI. Builds the same image App Platform ships,
# but mounts the source and runs uvicorn with --reload so edits to main.py and
# static/index.html apply without a rebuild. Connects outbound to the remote
# managed agent using AGENT_ENDPOINT / AGENT_API_KEY from .env — no DO services
# run locally and nothing is exposed inbound.
services:
  chat-ui:
    build:
      context: ./chat-ui
      dockerfile: Dockerfile
    ports:
      - "8080:8080"
    env_file:
      - ./.env
    volumes:
      - ./chat-ui:/app
    command: >
      uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

- [ ] **Step 3: Validate the compose file**

Run: `cp .env.example .env && docker compose config`
Expected: prints the resolved config with no error; `AGENT_ENDPOINT` shows the pre-filled URL. (Leave the copied `.env` in place for the verification in Task 5; it is gitignored.)

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "Add docker compose setup for local chat UI development"
```

---

## Task 3: Add the `agent_endpoint` Terraform output

**Files:**
- Modify: `deploy/outputs.tf`

- [ ] **Step 1: Add the output**

Append to `deploy/outputs.tf`:

```hcl
output "agent_endpoint" {
  value       = "${digitalocean_gradientai_agent.rag_agent.deployment[0].url}/api/v1/chat/completions"
  description = "OpenAI-compatible chat endpoint of the agent (for local dev AGENT_ENDPOINT)."
}
```

- [ ] **Step 2: Validate the Terraform config**

Run: `cd deploy && terraform fmt && terraform validate`
Expected: `Success! The configuration is valid.` (If `validate` reports the backend is not initialized, run `terraform init -backend=false` first, then re-run `terraform validate`.)

- [ ] **Step 3: Commit**

```bash
git add deploy/outputs.tf
git commit -m "Add agent_endpoint Terraform output for local dev"
```

---

## Task 4: Update the README "Local development" section

**Files:**
- Modify: `README.md` (the "### Local development" block, currently ~lines 264-275)

- [ ] **Step 1: Replace the section**

In `README.md`, replace the existing block:

```markdown
### Local development

To run the chat UI locally (requires a deployed agent):

```bash
cd chat-ui
pip install -r requirements.txt
export AGENT_UUID=<your-agent-uuid>
export DO_API_TOKEN=<your-token>
export AGENT_NAME="RAG Assistant"
uvicorn main:app --host 0.0.0.0 --port 8080
```
```

with:

````markdown
### Local development with Docker

The chat UI is a thin proxy to the remote DO-managed agent, so local development
runs **only the chat UI** in a container — it connects outbound to the agent over
HTTPS. The DO services (knowledge base, agent, guardrails) cannot run locally and
do not need to be exposed; the agent endpoint is gated by a secret API key.

Edits to `chat-ui/main.py` and `chat-ui/static/index.html` reload automatically.

**1. Create your `.env`:**

```bash
cp .env.example .env
```

`AGENT_ENDPOINT` is pre-filled with the current agent deployment URL. You only
need to supply `AGENT_API_KEY`.

**2. Mint an agent API key (one time).** Either:

- **Console:** [DO console](https://cloud.digitalocean.com/) → **GenAI Platform →
  Agents** → select the agent → **API Keys** → **Create Key** → copy the secret
  key.

- **API (`curl`):** with your DO API token and the agent UUID
  (`terraform output agent_uuid` from `deploy/`):

  ```bash
  curl -s -X POST \
    -H "Authorization: Bearer $DO_API_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"name":"local-dev"}' \
    "https://api.digitalocean.com/v2/gen-ai/agents/<AGENT_UUID>/api_keys" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['api_key_info']['secret_key'])"
  ```

Paste the key into `.env` as `AGENT_API_KEY`. The key is reused across restarts —
revoke it in the console when you no longer need it.

**3. (Only if the agent was recreated)** refresh the endpoint:

```bash
cd deploy && terraform output agent_endpoint   # paste into .env as AGENT_ENDPOINT
```

**4. Run it:**

```bash
docker compose up
```

Open <http://localhost:8080>.

> Running tests: `cd chat-ui && pip install -r requirements-dev.txt && python -m pytest`
````

- [ ] **Step 2: Verify the section renders and links are intact**

Run: `grep -n "Local development with Docker" README.md`
Expected: one match.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document local chat UI development with Docker Compose"
```

---

## Task 5: End-to-end verification

**Files:** none (manual verification)

- [ ] **Step 1: Ensure `.env` has a real key**

Confirm `.env` exists (copied in Task 2) and `AGENT_API_KEY` is filled in with a
real minted key. If not, mint one per the README and paste it in.

- [ ] **Step 2: Start the stack**

Run: `docker compose up --build`
Expected: logs show `Using AGENT_ENDPOINT/AGENT_API_KEY overrides; skipping DO API discovery` and `Uvicorn running on http://0.0.0.0:8080`.

- [ ] **Step 3: Health check**

Run (in another terminal): `curl -s localhost:8080/health`
Expected: `{"status":"ok","agent_ready":true}`

- [ ] **Step 4: Send a chat message**

Run: `curl -s -X POST localhost:8080/api/chat -H 'Content-Type: application/json' -d '{"message":"What does Evenbreak do?","history":[]}'`
Expected: JSON with a non-empty `content` field containing a grounded answer.

- [ ] **Step 5: Confirm live-reload**

Edit `chat-ui/static/index.html` (e.g. change the page title), reload
<http://localhost:8080>, and confirm the change appears without restarting the
container. Then `docker compose down`.

- [ ] **Step 6: Confirm `.env` is not tracked**

Run: `git status --porcelain .env`
Expected: no output (it is gitignored).

---

## Notes

- **App Platform behaviour is unchanged:** in production neither `AGENT_ENDPOINT`
  nor `AGENT_API_KEY` is set, so `_resolve_config` returns `True` and discovery
  runs exactly as before.
- **No DO services run locally** — this plan only containerises the proxy.
