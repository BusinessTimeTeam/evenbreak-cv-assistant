# App-Side Retrieval and Serverless Inference — Design

**Date:** 2026-10-28
**Status:** Draft (pending review)

## Why

The chat UI currently forwards every message to a DigitalOcean managed agent,
which retrieves from a DigitalOcean knowledge base (backed by an auto-provisioned
OpenSearch cluster) and runs three DO guardrails. This has been costly to live
with:

- **Bad answers** are when a guardrail refuses for no clear reason, even though
  similar questions and similar CVs get answered. The likely cause is the
  sensitive-data guardrail, whose default categories include names, dates,
  locations and nationality — the contents of every CV.
- **Great answers** are when the assistant pulls something genuinely useful from
  the knowledge base to guide the user in improving their CV. Retrieval quality
  is what we must protect.
- The Terraform for the agent and knowledge base needs many `ignore_changes`
  workarounds, the knowledge base leaks an OpenSearch cluster (~$19.60/month) on
  every destroy or recreate, and the guardrails cost ~$0.74 per million tokens
  (about 15× the input price of the model).

This is **step 1** of a rebuild. Later steps, each designed separately:

- **Step 3:** an app-side prompt-injection check. Content moderation and
  sensitive-data handling will be re-scoped deliberately, not copied.
- **Step 4:** strip the Terraform down and rebuild the stack from scratch on the
  latest DigitalOcean provider.
- **Step 2 (separate piece of work, later):** let users choose between cheaper
  and more expensive models, including Anthropic and OpenAI models.

## Goals

1. The chat UI no longer calls the managed agent. It retrieves relevant passages
   from `knowledge-base/data-sources/` itself and calls DigitalOcean serverless
   inference directly.
2. Answers are at least as useful as today's, judged by the user against a
   side-by-side comparison with the current agent.
3. The search index is rebuilt automatically on every push, with an easy local
   equivalent through Docker Compose. The built index is never committed to git.
4. A local, manually run scraper can turn web pages into knowledge-base
   documents. For now it is used only to capture the Evenbreak homepage.

## Non-goals

- Guardrails of any kind (step 3). Step 1 has none; it can only fail to answer if
  a model or API call fails.
- Removing Terraform resources for the agent, knowledge base, Spaces bucket or
  guardrails (step 4). They stay running so the comparison can call the old
  agent.
- Choosing between models in the UI (step 2).
- Showing citations or sources to users. The agent returns citations today, but
  the UI never displays them, so nothing visible is lost.
- Scheduled crawling, or scraping beyond the homepage.

## Anti-goals

- Retrieval that is noticeably worse than today's knowledge base, even if
  everything technically works.
- Silently mixing embedding models between the index and queries.
- Getting around the site's bot protection. The scraper identifies itself
  honestly and respects robots.txt; access problems are solved by asking
  Evenbreak to whitelist it.
- Committing the built index, or putting any real person's CV into the repo.

## How a chat turn works

```
browser ──> chat-ui /api/chat
              │ 1. rewrite: small model call turns latest message + recent
              │    history + CV into a few search queries
              │ 2. embed the queries (Embeddings API, same model as the index)
              │ 3. cosine search over the in-memory index, merge, top k
              │ 4. chat completion: system prompt + CV + retrieved passages
              │    + history + message
              ▼
       DO serverless inference (https://inference.do-ai.run/v1)
```

### Retrieval

- **Index contents:** every chunk's text, its source file path, and its
  embedding, plus metadata: the embedding model name, vector dimension, a hash of
  the source documents, and the build time.
- **Embedding model:** `Qwen3 Embedding 0.6B`, the same model today's knowledge
  base uses, so the comparison tests the retrieval code rather than a model
  change. *(My call. Trying BGE-M3 later is a cheap experiment once the
  comparison exists.)*
- **Model-mismatch guard:** the app refuses to start if the index was built with
  a different embedding model from the one it is configured to embed queries
  with.
- **Search:** brute-force cosine similarity in memory (numpy). The corpus is
  about 300k tokens, roughly a thousand chunks; this stays fast and small up to
  about 10M tokens.
- **Swappable storage:** retrieval sits behind a small interface (embed texts;
  find the top k chunks for a query), so moving to pgvector or a vector database
  later changes one module.
- **k = 10,** matching the current `agent_k`.
- **Query rewriting** replaces the agent's `RETRIEVAL_METHOD_SUB_QUERIES`.
  Searching on the latest message alone would break follow-ups like "what about
  my second job?". A small model call (the same `gpt-oss-20b`) produces a few
  search queries; results are merged and de-duplicated before taking the top k.
  If the rewrite call fails, fall back to searching on the raw message. *(My
  call.)*

### Generation

- **Model:** `gpt-oss-20b` on serverless inference, the model used today, so the
  comparison isolates retrieval. Same settings as today: temperature 0.2,
  max tokens 2048, top_p 1.
- **System prompt:** the current `agent_instruction` text from
  `deploy/terraform.tfvars`, moved into a file in `chat-ui/` so it lives with the
  app code. *(My call.)* It is sent as a real `system` message; the agent
  rejected those, serverless inference does not. Its last paragraph refers to
  "the supplied knowledge-base context", which still fits.
- **CV:** injected as today (`_cv_message` in `chat-ui/main.py`).
- **Retrieved passages:** clearly delimited and labelled with their source file
  names. Exact placement in the message list is the builder's call.
- **Errors:** an embedding or inference failure produces a readable error reply,
  in the same spirit as the current `_call_agent` error handling.

### Configuration

New environment variables for the app:

- `MODEL_ACCESS_KEY` (secret) — a DigitalOcean model access key.
- `INFERENCE_BASE_URL` — default `https://inference.do-ai.run/v1`.
- `CHAT_MODEL` — default to the serverless slug for gpt-oss-20b (builder
  confirms the exact slug from the models list).
- `EMBEDDING_MODEL` — default `qwen3-embedding-0.6b`. DO's "Generate vector
  embeddings" guide lists Qwen3 Embedding 0.6B on the serverless Embeddings API
  (`https://inference.do-ai.run`, model access key) under that slug. If it
  turns out not to be callable there, stop and come back to the user rather than
  switching model or running embeddings locally. *(My call.)*
- `INDEX_PATH` — where the built index lives.

`AGENT_ENDPOINT` and `AGENT_API_KEY` are no longer used by the app. They are
still needed by the comparison script.

## Building the index

- **Inputs:** everything under `knowledge-base/data-sources/`. Today that is 86
  `.md`, 1 `.txt` and 4 `.pdf` files. PDFs are read with `pypdf`, already a
  dependency. Unsupported file types are skipped with a warning.
- **Chunking:** start around 500 tokens with modest overlap, splitting on
  markdown headings and paragraphs where possible. Tuning is the builder's call.
  Note that one PDF ("Global Disability Incl…") is about 145k of the corpus's
  ~235k words and will dominate the chunk count.
- **Cost:** about 1¢ and well under a minute per full rebuild.
- **Where it runs:** a dedicated `index` stage in `chat-ui/Dockerfile`. It
  copies in `knowledge-base/data-sources/`, embeds everything, and writes the
  index file. The production stage copies only the index file from that stage,
  so the model access key used to build it never reaches the final image.
- **Automatic on push:** App Platform already rebuilds on every push to `main`.
  For the build to see `knowledge-base/`, the App Platform build context moves
  from `chat-ui/` to the repository root (`source_dir`), with the Dockerfile
  still at `chat-ui/Dockerfile`. A failed index build fails the deploy, and App
  Platform keeps serving the previous version.
- **To verify early:** how App Platform passes a secret environment variable to
  a Dockerfile build. It is believed to arrive as a build argument. If it can't,
  fall back to building the index when the container starts, cached by the
  source-document hash.
- **Locally:** `docker compose up` builds the same `index` stage, so local
  development gets a fresh index automatically. Docker's layer cache means it
  only re-embeds when a file in `knowledge-base/` changes. A forced rebuild is a
  one-line `docker compose build` command, documented in the README.
- **Watch out:** `docker-compose.yml` mounts `./chat-ui` over `/app` for live
  reload, which would hide an index file baked into `/app`. The index must live
  outside the mounted path, located by `INDEX_PATH`.
- **Never committed:** add the index output to `.gitignore`.

## Deploying step 1

The Terraform changes needed to run the new version on App Platform are part of
step 1:

- the app's `source_dir` and `dockerfile_path`
- the new environment variables, with `MODEL_ACCESS_KEY` available at both build
  and run time
- a `MODEL_ACCESS_KEY` secret in the GitHub Actions workflow
- removing `lifecycle { ignore_changes = [spec[0].service[0].env] }` from
  `deploy/app.tf`, so the new variables actually reach the app. The ignore was
  there because secret env values always look like drift, so every apply will
  now redeploy the app. That's acceptable: document changes redeploy it anyway
  now. Step 4 revisits it. *(My call.)*

Removing the old resources waits for step 4.

**Branches:** App Platform deploys from `main` (`_app_source_branch`). `master`
is the remote's default branch but is behind `main` and has nothing `main`
lacks. Build on a feature branch off `main` and merge into `main`. *(My call.)*

Pushing to `main` deploys, so **step 1 is not merged until the user has reviewed
the comparison and is happy with it.** Until then, the new version runs locally
through Docker Compose. *(My call.)*

## The scraper

- A local script, run manually, using **trafilatura** to extract a page's main
  content as clean markdown. It drops navigation, cookie banners and footers.
- **Inputs:** a URL (and, for later, a sitemap), or a locally saved HTML file
  together with its original URL. The saved-file mode exists because
  evenbreak.com currently returns a 403 from Madgex bot protection to any
  automated request, trafilatura included. The user will ask Evenbreak to
  whitelist the scraper if needed.
- **Output:** one markdown file per page under
  `knowledge-base/data-sources/web/`, with a short header recording the source URL
  and the date it was captured.
- **Behaviour:**
  - an honest user agent such as
    `EvenbreakCVAssistant/1.0 (+contact: mail@mayortech.co.uk)`, so Evenbreak can
    whitelist it by name
  - respect robots.txt
  - pause between requests
- **Packaging:** runs through a Docker Compose service, like the test service.
  trafilatura stays out of the app's production image. *(My call.)*
- **Homepage for step 1:** extracted from the user's saved copy of
  https://www.evenbreak.com/ (at
  `~/Downloads/Evenbreak - Award-winning Job board for disabled people.htm`; the
  saved HTML itself is not committed) and the resulting markdown committed as-is, including the live job-count
  lists, to keep the comparison like-for-like with today's crawler. A test
  extraction kept 374 of 687 visible words and dropped the navigation, banner
  and footer correctly.

## The comparison

- **Test set:** I draft 10–15 realistic user questions written from the course
  material (adjustments, disclosure, employment gaps, inclusive wording, and so
  on), and 2–3 invented CVs. **The user reviews the drafts before they're used.**
  Some questions are paired with a CV, and a few are multi-turn, so follow-up
  retrieval is tested.
- **Script:** runs every case through both the live old agent
  (`AGENT_ENDPOINT` / `AGENT_API_KEY`) and the new pipeline. The CV is injected
  the same way in both.
- **Report:** a side-by-side report for the user to read. No automatic scoring.
  For the new pipeline it also lists which source files were retrieved, to help
  diagnose a weak answer.
- **Baseline:** the old agent's answers are saved and committed, so they
  survive the step 4 teardown. They contain only invented CVs. *(My call.)*
- **Timing:** the comparison must be run before step 4 destroys the old agent.

## Constraints

- Build on the existing app: Python 3.14, FastAPI, httpx, the multi-stage
  Dockerfile, the Docker Compose workflow, and the pytest style in
  `chat-ui/tests/` (offline, with network calls stubbed via monkeypatch).
- Keep the production image lean. numpy is fine; no vector database, no
  LangChain-style framework.
- Use the `user` role for the CV message, as today.

## Testing

Offline unit tests, with network calls stubbed:

- chunking
- index build and load, including the model-mismatch refusal
- top-k search over small fake vectors
- merging and de-duplicating results from several queries
- falling back to the raw message when query rewriting fails
- message assembly: system prompt, CV, passages, history
- scraper extraction from a small hand-written HTML fixture, not a copy of
  Evenbreak's page *(my call)*

Existing tests that stub `_call_agent` are updated to stub the new inference
call instead.

## Left to the builder

- chunk size and overlap
- the index file format
- the query-rewriting prompt
- module layout
- where the retrieved passages sit in the message list
- whether the scraper's Compose service reuses the app image or has its own
  small one
