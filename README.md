# Evenbreak CV Assistant

This project was forked from the DigitalOcean marketplace-blueprints repo in GitLab. It
was originally licensed under the Apache 2 licence.

This stack deploys a fully functional Retrieval-Augmented Generation (RAG) assistant on DigitalOcean, including:

- A **managed GenAI agent** with serverless inference for question answering.
- A **Knowledge Base** (KBaaS) for document storage and semantic retrieval.
- An **App Platform** service hosting a chat UI that proxies requests to the agent.
- **Guardrails** for jailbreak detection, content moderation, and sensitive data protection.
- A **DigitalOcean project** to group all provisioned resources.

The agent, knowledge base, chat UI, and guardrails are wired together out of the box.

## How to use this blueprint?

Learn [here](../../README.md#how-to-use-digitalocean-blueprints) how to use this blueprint.

## Architecture

```
User  ──>  Chat UI (App Platform)
               │
               ▼
         Managed Agent  ──>  Guardrails (jailbreak / content / PII)
               │
               ▼
         Knowledge Base  ──>  Embedding Model (Qwen3 0.6B)
               │
               ▼
       Serverless Inference (configurable model)
               │
               ▼
           Response
```

The query flow works as follows:

1. The user sends a message through the Chat UI.
2. The App Platform service forwards the message to the managed agent's OpenAI-compatible endpoint.
3. The agent runs the query through attached guardrails (jailbreak, content moderation, sensitive data).
4. The agent retrieves relevant document chunks from the Knowledge Base using semantic search.
5. Retrieved context is assembled into a prompt and sent to the serverless inference model.
6. The response passes back through guardrails and is returned to the user.

## Getting started

After the stack is deployed, allow 2-3 minutes for the knowledge base to finish indexing its initial data source and for the App Platform build to complete.

### 1. Access the Chat UI

The chat UI URL is available in the Terraform outputs (`chat_ui_url`). Open it in your browser to see the assistant interface.

### 2. Upload your documents

The knowledge base is seeded with a placeholder DigitalOcean docs page. To use your own data:

1. Go to the [DigitalOcean console](https://cloud.digitalocean.com/).
2. Navigate to **GenAI Platform > Knowledge Bases**.
3. Select the knowledge base created by this stack (named `<basename>-<suffix>-kb`).
4. Add your documents — supported formats include web URLs, PDFs, and plain text.
5. Wait for indexing to complete, then ask the assistant questions about your content.

### 3. Ask questions

Use the chat interface to ask questions. The assistant will search your knowledge base documents and provide grounded answers with citations when available.

## Terraform variables

| Variable | Default | Description |
|---|---|---|
| `do_token` | *(required)* | DigitalOcean API token |
| `agent_api_key` | *(required)* | Secret API key the chat UI uses to call the agent. Mint one out-of-band (no provider resource exists); supply via `TF_VAR_agent_api_key` (CI: the `AGENT_API_KEY` repo secret) |
| `project_uuid` | `""` | Existing project UUID (leave empty to create a new project) |
| `basename` | `rag-assistant` | Base name used to auto-generate resource names |
| `project_name` | `""` | Display name for the project (defaults to `basename`) |
| `region` | `nyc3` | DigitalOcean region for App Platform resources |
| `default_model` | `nvidia-nemotron-3-super-120b` | Serverless inference model name |
| `model_uuid` | *(required)* | UUID of the inference model (resolved by do-terraform) |
| `embedding_model` | `qwen3-embedding-0.6b` | Embedding model name |
| `embedding_model_uuid` | *(required)* | UUID of the embedding model (resolved by do-terraform) |
| `app_instance_size` | `apps-s-1vcpu-1gb` | App Platform instance size slug |
| `agent_instruction` | *(see variables.tf)* | System instruction for the agent |
| `agent_temperature` | `0` | Inference temperature (0 = deterministic) |
| `agent_max_tokens` | `4096` | Maximum tokens in the agent response |
| `agent_k` | `5` | Number of KB documents to retrieve per query |
| `guardrail_jailbreak_uuid` | `""` | UUID of the jailbreak detection guardrail |
| `guardrail_content_mod_uuid` | `""` | UUID of the content moderation guardrail |
| `guardrail_sensitive_data_uuid` | `""` | UUID of the sensitive data detection guardrail |

## Terraform outputs

| Output | Description |
|---|---|
| `chat_ui_url` | URL of the deployed chat UI application |
| `agent_uuid` | UUID of the managed RAG agent |
| `knowledge_base_uuid` | UUID of the knowledge base |
| `project_id` | Project ID containing all resources |
| `app_platform_id` | App Platform resource ID |
| `agent_id` | GenAI agent resource ID |
| `knowledge_base_id` | Knowledge base resource ID |

## Stack details

- **GenAI region**: The agent and knowledge base are deployed to `tor1` (the only region currently supporting the GenAI platform).
- **App Platform region**: Configurable via the `region` variable (default `nyc3`).
- **Inference**: Serverless — no GPU instances to manage. The model is configurable via model presets when deployed through do-terraform.
- **Embeddings**: Qwen3 0.6B is used by default for document embedding.
- **Guardrails**: Jailbreak detection, content moderation, and sensitive data protection are attached post-creation via the DO API (terraform provider limitation).
- **KB indexing**: A `null_resource` provisioner waits up to 10 minutes for knowledge base indexing to complete before attaching it to the agent.
- **Chat UI**: A Python FastAPI application deployed on App Platform. The agent endpoint and API key are injected as env vars (`AGENT_ENDPOINT` / `AGENT_API_KEY`); the app does no DO API discovery or key generation. Because the provider has no resource to mint an agent API key, the key is operator-supplied (`var.agent_api_key`).
- **Resource naming**: All resources are suffixed with a random 4-character string to avoid naming collisions.

## Known issues

### The knowledge base's OpenSearch database is not Terraform-managed, and leaks on destroy

A DO GenAI knowledge base requires an OpenSearch cluster as its vector store. The
platform **auto-provisions its own managed cluster** (named `genai-*`) when the KB
is created and records it in the KB's `database_id`.

- **You cannot bring your own cluster.** We tried creating a
  `digitalocean_database_cluster` and passing its id as the KB's `database_id`;
  the platform ignored it and created its own anyway. So `database_id` is a
  server-managed value and is listed in the KB's `ignore_changes`.
- **Cost.** The auto-created cluster is the smallest OpenSearch tier
  (`db-s-1vcpu-2gb`, ~$19.60/month) and is mandatory for RAG — there is no
  smaller or serverless option.
- **It leaks on destroy.** `terraform destroy` deletes the KB but **not** its
  OpenSearch cluster. The same happens on any KB *recreation*. After a destroy or
  recreate, delete the orphaned cluster manually so you stop paying for it:

  ```bash
  doctl databases list            # find the orphaned genai-* opensearch cluster
  doctl databases delete <id>
  ```

### The knowledge base's datasources are ignored by Terraform

The KB requires at least one *inline* datasource; the inline web crawler (the
Evenbreak homepage) satisfies that. The Spaces document bucket is attached as a
**separate** `knowledge_base_data_source` resource (`spaces_docs`), which the API
then reports back as an extra datasource on the KB.

Because the KB's `datasources` attribute is **force-new**, that mismatch would
make every `terraform apply` *recreate the KB* (which also strands an OpenSearch
cluster — see above). To prevent that, `datasources` is in the KB's
`ignore_changes`.

Consequences:

- Editing the inline web-crawler URL **in config has no effect** (it's ignored),
  and editing it directly in the console forces a KB recreation.
- To add or remove document sources **without** recreating the KB, manage them as
  separate `digitalocean_gradientai_knowledge_base_data_source` resources (like
  `spaces_docs`) rather than editing the KB's inline `datasources` block.

## Chat UI

The chat UI is a lightweight FastAPI application located in `chat-ui/`. It:

- Serves a single-page web interface with a conversational chat layout.
- Proxies messages to the managed agent's OpenAI-compatible chat completions endpoint.
- Reads the agent endpoint and API key from the environment (`AGENT_ENDPOINT` / `AGENT_API_KEY`) — it never calls the DO API to discover the endpoint or mint a key.
- Maintains conversation history for multi-turn interactions.
- Accepts CV uploads (PDF / `.docx`) via `POST /api/upload`. Because the managed
  agent only accepts text (and is stateless — it keeps no session of its own),
  the app extracts the document's text server-side (`extraction.py`, using
  `pypdf` / `python-docx`), stores it keyed by a **session id**, and returns that
  id to the browser. On every `/api/chat` turn the browser sends the session id
  back and the server re-injects the CV as a stable leading message, so the agent
  can reason about it across the whole conversation. The CV is carried as a
  `user` message, **not** a `system` message: the managed agent rejects
  client-supplied system/developer messages (`HTTP 400 — "agent instructions are
  set via agent configuration"`). The browser only holds the
  session id and the Q&A turns — never the full CV text. The session store is an
  in-process dict (`_cv_sessions`), fine for the current single instance but lost
  on restart; move it to a shared store (Redis/Spaces) if the app scales out.
  Prompt caching is intentionally not used: it only applies to Anthropic/OpenAI
  models, and this stack defaults to `nvidia-nemotron-3-super-120b`. The stable
  leading-message structure is cache-ready if the model is later switched. The
  bare upload form in `static/index.html` is a placeholder for a proper frontend.

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

**2. Mint an agent API key (one time).**:

- **Console:** [DO console](https://cloud.digitalocean.com/) → **GenAI Platform →
  Agents** → select the agent → **API Keys** → **Create Key** → copy the secret
  key.

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

## Security

- The `do_token` variable is marked as sensitive and will not appear in Terraform plan output.
- The agent API key is injected as a `SECRET` environment variable in App Platform.
- Guardrails provide defense-in-depth against prompt injection, toxic content, and PII leakage.
- The chat UI does not store conversation history server-side; all history is held in the browser session.

## Future work

For this first MVP we've reduced the scope in some areas. This is a list of the things
that we think we could come back to at a later date:

- Use Function Calling on the agent to fetch uploaded file contents, instead of having
  the file's contents extracted by our server. This would mean that the user would
  upload the file to DO Spaces, and we'd write an agent skill to fetch file contents.
- Upgrade the model to one of the Anthropic/OpenAI models in order to use their context
  cache features. This should speed up conversations.
