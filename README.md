# Evenbreak CV Assistant

This project was forked from the DigitalOcean marketplace-blueprints repo in GitLab. It
was originally licensed under the Apache 2 licence.

A UK-focused CV, employment and disability-inclusion assistant. The stack deploys:

- An **App Platform** service hosting the chat UI, which does its own retrieval
  over a knowledge-base index built into its image.
- **DigitalOcean serverless inference** for embeddings (Qwen3 Embedding 0.6B) and
  chat (gpt-oss-20b), called directly with a model access key.
- A **DigitalOcean project** to group the provisioned resources.

## Architecture

```
Build:  knowledge-base/data-sources/  ──>  chunk + embed  ──>  index in the image

Chat:   User  ──>  Chat UI (App Platform)
                       │  rewrite the message into search queries
                       │  embed them and search the in-memory index
                       ▼
              Serverless inference (gpt-oss-20b)  ──>  Response
```

The query flow works as follows:

1. The user sends a message through the Chat UI.
2. The message is checked by a guardrail model call and blocked if it looks like
   an attack; uploaded CVs get the same check. A small model call then rewrites the message (with recent history and the CV) into
   a few standalone search queries.
3. The queries are embedded and the closest knowledge-base passages are found by
   cosine similarity over the index.
4. The system prompt (`chat-ui/prompts/system.md`), CV, history, passages and
   message are sent to the chat model, and its answer is returned with the
   passages' sources.

## Terraform variables

| Variable | Default | Description |
|---|---|---|
| `do_token` | *(required)* | DigitalOcean API token |
| `model_access_key` | *(required)* | DO model access key the chat UI uses for serverless inference, at build time (embedding the knowledge base) and run time (chat). Supply via `TF_VAR_model_access_key` (CI: the `MODEL_ACCESS_KEY` repo secret) |
| `project_uuid` | `""` | Existing project UUID (leave empty to create a new project) |
| `basename` | `rag-assistant` | Base name used to auto-generate resource names |
| `project_name` | `""` | Display name for the project (defaults to `basename`) |
| `region` | `nyc3` | DigitalOcean region for App Platform resources |
| `app_instance_size` | `apps-s-1vcpu-1gb` | App Platform instance size slug |

The app's env is not in `ignore_changes`, so every `terraform apply` redeploys
the app (the secret always looks like drift). That is expected: document changes
need a redeploy anyway to rebuild the index. After a deploy, the App Platform
build log shows the `index` stage embedding the documents (`Indexed N chunks ...`).

Terraform state lives in the `evenbreak-cv-assistant-tfstate` Spaces bucket,
managed outside Terraform (see `deploy/backend.tf`). The state backend reads its
credentials from `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (CI: the
`SPACES_ACCESS_ID` / `SPACES_SECRET_KEY` repo secrets).

## Terraform outputs

| Output | Description |
|---|---|
| `app_url` | URL of the deployed chat UI application |
| `project_id` | Project ID containing all resources |
| `app_platform_id` | App Platform resource ID |

## Stack details

- **App Platform region**: Configurable via the `region` variable (default `nyc3`).
- **Inference**: Serverless — no GPU instances to manage.
- **Resource naming**: All resources are suffixed with a random 4-character string to avoid naming collisions.

## Chat UI

The chat UI is a lightweight FastAPI application located in `chat-ui/`. It:

- Serves a single-page web interface with a conversational chat layout.
- Answers each message with app-side retrieval plus serverless inference (see
  Architecture), reading `MODEL_ACCESS_KEY` from the environment.
- Maintains conversation history for multi-turn interactions. The browser holds
  the history, signed with `HISTORY_SIGNING_KEY` so it cannot be tampered with;
  Terraform generates the key (`random_password.history_signing_key`) and passes
  it to the app as a `SECRET` env var, and locally it comes from `.env`.
- Accepts CV uploads (PDF / `.docx`) via `POST /api/upload`. The app extracts the
  document's text server-side (`extraction.py`, using `pypdf` / `python-docx`),
  stores it keyed by a **session id**, and returns that id to the browser. On
  every `/api/chat` turn the browser sends the session id back and the server
  re-injects the CV as a stable leading message, so the model can reason about it
  across the whole conversation. The browser only holds the session id and the
  Q&A turns — never the full CV text. The session store is an in-process dict
  (`_cv_sessions`), fine for the current single instance but lost on restart;
  move it to a shared store (Redis/Spaces) if the app scales out. The bare upload
  form in `static/index.html` is a placeholder for a proper frontend.

### Local development with Docker

The chat UI answers questions itself: it searches a knowledge-base index built
into its image, then calls DigitalOcean serverless inference. Local development
runs **only the chat UI** in a container; it connects outbound to serverless
inference over HTTPS with your model access key.

Edits to `chat-ui/main.py` and `chat-ui/static/index.html` reload automatically.

**1. Create your `.env`:**

```bash
cp .env.example .env
```

**2. Create a model access key (one time):** [DO console](https://cloud.digitalocean.com/model-studio/manage-keys)
→ **Create model access key** → **Select models**: gpt-oss-20b and Qwen3
Embedding 0.6B → **No VPC network** → copy the secret (it is shown once) into
`.env` as `MODEL_ACCESS_KEY`.

**3. Run it:**

```bash
docker compose up --build
```

Open <http://localhost:8080>.

The first build embeds everything in `knowledge-base/data-sources/` into the
image (about a minute, about 1¢). Later builds reuse the cached index unless a
document changed. After adding or editing documents, rebuild with:

```bash
docker compose build chat-ui
```

The index is built on every deploy too, so it is never committed to git.

> Running tests: `docker compose run --rm chat-ui-test`

**Guardrail evals.** `evals/guardrails_eval.py` runs the message and CV checks
against the labelled cases in `evals/guardrail_cases.json` (CV fixtures in
`evals/guardrail-cvs/` and `evals/cvs/`), using the real model and your `.env`
key (a fraction of a penny per run):

```bash
docker compose run --rm evals python /evals/guardrails_eval.py            # all cases
docker compose run --rm evals python /evals/guardrails_eval.py --case ID  # one case
```

It prints PASS/FAIL per case, writes `evals/results/<timestamp>/guardrails.json`
(not committed) and exits 1 on any **false block** (a genuine message or CV
refused). Missed blocks are reported but tolerated. The model's verdicts vary a
little between runs, so run it more than once after changing the prompts in
`chat-ui/guardrails.py`.

### Adding web pages to the knowledge base

`tools/scrape/` turns web pages into markdown documents in
`knowledge-base/data-sources/web/`, using
[trafilatura](https://trafilatura.readthedocs.io/) to keep each page's main
content and drop navigation, cookie banners and footers. Run it by hand when you
want to add or refresh pages, review the output, and commit it:

```bash
docker compose run --rm scrape https://www.example.org/page/          # one or more pages
docker compose run --rm scrape --sitemap https://www.example.org/sitemap.xml
```

The scraper identifies itself as
`EvenbreakCVAssistant/1.0 (+contact: mail@mayortech.co.uk)`, respects robots.txt
and waits 2 seconds between requests (`--delay`). evenbreak.com currently returns
**403 Access Denied** to automated requests, so ask Evenbreak to whitelist that
user agent. Until then, save a page from your browser (**Save Page As… → HTML
only**) into `tools/scrape/input/` (gitignored) and run:

```bash
docker compose run --rm scrape --html /input/page.htm --url https://www.evenbreak.com/the-page/
```

Scraper tests: `docker compose run --rm --entrypoint python scrape -m pytest tests`

## Security

- The `do_token` variable is marked as sensitive and will not appear in Terraform plan output.
- The model access key is injected as a `SECRET` environment variable in App Platform. It is
  also a build arg (to embed the knowledge base), so never reference it in a Dockerfile `RUN`
  line: build output expands build args.
- Chat messages and CV uploads pass an app-side prompt-injection check before being answered.
- `HISTORY_SIGNING_KEY` is generated by Terraform, held in state and injected as a `SECRET`
  env var; rotating it invalidates conversations in progress.
- The chat UI does not store conversation history server-side; all history is held in the browser session.

## Future work

For this first MVP we've reduced the scope in some areas. This is a list of the things
that we think we could come back to at a later date:

- Upgrade the model to one of the Anthropic/OpenAI models in order to use their context
  cache features. This should speed up conversations.
