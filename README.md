# Evenbreak CV Assistant

This project was forked from the DigitalOcean marketplace-blueprints repo in GitLab. It
was originally licensed under the Apache 2 licence.

A UK-focused CV, employment and disability-inclusion assistant. The stack deploys:

- A **Dokku droplet** in London (`lon1`, 2GB RAM) hosting the chat UI on
  `evenbreak-cv.businesstime.team`, which does its own retrieval over a
  knowledge-base index built into its image. It replaces App Platform, whose
  100s request limit and response buffering break streamed replies.
- An **App Platform** service hosting the same chat UI. It is being retired:
  it runs side by side with the droplet until the droplet is confirmed working,
  then it is removed.
- **DigitalOcean serverless inference** for embeddings (Qwen3 Embedding 0.6B) and
  chat (gpt-oss-20b, or the model the user picks during the model trial), called
  directly with a model access key.
- A **DigitalOcean project** to group the provisioned resources.

## Architecture

```
Build:  knowledge-base/data-sources/  ──>  chunk + embed  ──>  index in the image

Chat:   User  ──>  Chat UI (Dokku droplet; App Platform until it is retired)
                       │  rewrite the message into search queries
                       │  embed them and search the in-memory index
                       ▼
              Serverless inference (gpt-oss-20b)  ──>  Response
```

The query flow works as follows:

1. The user sends a message through the Chat UI.
2. A guardrail model call checks the message for prompt injection and off-topic
   requests (uploaded CVs get a similar check). A small model call then rewrites
   the message (with recent history and the CV) into a few standalone search
   queries.
3. The queries are embedded and the closest knowledge-base passages are found by
   cosine similarity over the index.
4. The system prompt (`chat-ui/prompts/system.md`), CV, history, passages and
   message are sent to the chat model, and its answer is returned with the
   passages' sources.

## Model trial (temporary)

While we choose the long-term chat model, a select group of users picks the
model for each conversation on the landing page. The choice locks once the
conversation starts (a CV upload or "Ask a question"); going back starts a new
conversation. Only the answer uses the chosen model: the guardrail checks and
query rewriting stay on gpt-oss-20b, and embeddings on Qwen3 Embedding 0.6B.

A strip under the header shows the model and the conversation's running cost,
covering every model call (CV check, guardrails, query rewriting, embeddings and
answers), worked out from each response's token usage and the prices in
`chat-ui/trial.py`. The model and total are signed into the history token.

Each turn (and each CV upload) logs one line for comparing cost per
conversation, with token counts by purpose and never any message or CV text:

```
Turn cost: kind=chat outcome=answered session=qa-… model=deepseek-4-flash reply_in=3481 reply_out=686 guard_in=565 guard_out=131 rewrite_in=167 rewrite_out=280 embed_in=48 turn_usd=0.000903 total_usd=0.000903
```

The trial models are gpt-oss-20b, DeepSeek V4 Flash, Qwen 3.5 397B, DeepSeek V4 Pro, GLM-5.3 and Kimi K3. Claude Haiku 5.5, GPT-6 Luna,
Claude Sonnet 5.5 and GPT-6 Sol are commented out in `chat-ui/trial.py` until
the DigitalOcean account's subscription tier allows them. Check every trial
model against the API (IDs, access, request settings) with:

```bash
docker compose --profile tools run --rm evals python /app/check_models.py
```

To remove the trial, follow the docstring at the top of `chat-ui/trial.py`.

## Hosting on Dokku

The droplet (`deploy/droplet.tf`) is disposable. On first boot, cloud-init
(`deploy/cloud-init.yaml.tftpl`) installs Dokku and the Let's Encrypt plugin,
creates the `chat-ui` app (deploy branch `main`, built from `chat-ui/Dockerfile`,
healthcheck in `chat-ui/app.json`), sets its secrets and nginx settings
(no response buffering, 600s reads, 10MB uploads), and adds the deploy key last.
The app itself arrives with the next deploy. Any change to that setup, including
a rotated secret, replaces the droplet. The reserved IP and the SSH host key come
from Terraform, so DNS and `known_hosts` never change when it is replaced. The
firewall lets in only ports 22, 80 and 443.

Deploys (`.github/workflows/deploy.yml`) push `main` to
`ssh://dokku@<reserved_ip>:22/chat-ui` with `dokku/github-action`, and run:

- on every push to `main`
- after every Terraform apply (`terraform-apply.yml` starts one), in case the
  apply replaced the droplet
- by hand from the Actions tab

Each deploy first waits, up to 20 minutes, until
`ssh dokku@<reserved_ip> apps:exists chat-ui` succeeds, which means a new
droplet has finished setting up. After pushing it enables the Let's Encrypt
certificate if it is not active yet. That step only warns if it fails, because
it fails until the domain's A record points at the reserved IP.

### Setting up a new server

1. In `deploy/terraform.tfvars`, set `letsencrypt_email` and
   `admin_ssh_public_keys` (your own SSH public keys, for logging in as root;
   Terraform registers them with DigitalOcean, so they mustn't already be
   registered in the account),
   and commit it: CI reads this file, so a CI apply fails without them.
2. Run `terraform apply` in `deploy/`, or merge to `main` and let CI apply it.
   Merging to `main` also redeploys the App Platform app until it's removed.
   Before a local apply:
   - `deploy/env.sh` must export `TF_VAR_model_access_key` with exactly the
     value of the `MODEL_ACCESS_KEY` GitHub secret. Without it, Terraform
     prompts for the key. With a different value, the next CI apply renders
     different `user_data` and replaces the droplet.
   - Check that no Terraform Apply run is in progress, and don't push to `main`
     until the local apply has finished. The Spaces state backend has no
     locking, so overlapping applies can corrupt the state.
3. At the domain's DNS host, add an A record for
   `evenbreak-cv.businesstime.team` pointing at `terraform output -raw reserved_ip`.
   This is needed once: the reserved IP survives droplet rebuilds. The domain's
   DNS is on Cloudflare: make the record **DNS only** (grey cloud), not proxied.
   Through the proxy, Cloudflare reaches the droplet over plain HTTP, Dokku
   redirects that to HTTPS, and browsers loop; the proxy's 100-second timeout
   could also cut off long streamed replies.
4. Copy the outputs into the GitHub repo's Actions settings, once:

   | GitHub setting | Kind | Value |
   |---|---|---|
   | `DOKKU_SSH_PRIVATE_KEY` | secret | `terraform output -raw dokku_deploy_private_key` |
   | `DOKKU_KNOWN_HOSTS` | secret | `terraform output -raw dokku_ssh_known_hosts` |
   | `DOKKU_HOST` | variable | `terraform output -raw reserved_ip` |

5. Deploy the app. Every apply that creates or replaces the droplet starts the
   **Deploy** workflow on `main` for you (`terraform_data.deploy_new_droplet`,
   through `deploy/trigger-deploy.sh` and the `gh` CLI, so a local apply needs
   `gh auth login`). The workflow waits for the droplet to finish setting up,
   deploys, and enables HTTPS. A wrong `DOKKU_KNOWN_HOSTS` or deploy key looks
   the same as a droplet that is still setting up, so it only fails after the
   20-minute readiness wait. Until this branch is merged, `deploy.yml` isn't on
   `main` and the apply only warns: deploy by hand with `deploy/push.sh`.

After this first setup, rebuilding the server (for example after changing the
setup script or rotating a secret) is just `terraform apply`: the reserved IP,
host key and GitHub settings stay the same, and the new droplet deploys itself.

### If the first boot fails

If deploys keep waiting, or the app never appears, check the first-boot setup
over SSH (as root, with one of the `admin_ssh_public_keys`):

```bash
ssh root@<reserved_ip> cloud-init status --long
ssh root@<reserved_ip> tail -n 100 /var/log/cloud-init-output.log
```

The setup runs only once per droplet, so after fixing the cause, rebuild the
droplet rather than re-running it:

```bash
cd deploy && source env.sh && terraform apply -replace=digitalocean_droplet.chat
```

### Security note

The droplet's `user_data` holds the app's secrets and the SSH host private
key. Anything running on the droplet, including the app's container, can read
it from the DigitalOcean metadata endpoint (`http://169.254.169.254`). The
setup script deletes its own copy of the secrets, but the metadata endpoint
keeps them for the droplet's lifetime. That is accepted for the MVP.

## GitHub Actions secrets and variables

| Name | Kind | Used by | Value |
|---|---|---|---|
| `DO_TOKEN` | secret | Terraform Apply | DigitalOcean API token (`TF_VAR_do_token`) |
| `MODEL_ACCESS_KEY` | secret | Terraform Apply | Model access key (`TF_VAR_model_access_key`) |
| `SPACES_ACCESS_ID` | secret | Terraform Apply | Spaces key ID for the state backend (`AWS_ACCESS_KEY_ID`) |
| `SPACES_SECRET_KEY` | secret | Terraform Apply | Spaces secret for the state backend (`AWS_SECRET_ACCESS_KEY`) |
| `DOKKU_SSH_PRIVATE_KEY` | secret | Deploy | `terraform output -raw dokku_deploy_private_key` |
| `DOKKU_KNOWN_HOSTS` | secret | Deploy | `terraform output -raw dokku_ssh_known_hosts` |
| `DOKKU_HOST` | variable | Deploy | `terraform output -raw reserved_ip` |

## Terraform variables

| Variable | Default | Description |
|---|---|---|
| `do_token` | *(required)* | DigitalOcean API token |
| `model_access_key` | *(required)* | DO model access key the chat UI uses for serverless inference at run time (embedding queries and chat); image builds don't need it. Supply via `TF_VAR_model_access_key` (CI: the `MODEL_ACCESS_KEY` repo secret) |
| `project_uuid` | `""` | Existing project UUID (leave empty to create a new project) |
| `basename` | `cv-assistant` | Base name used to auto-generate resource names |
| `project_name` | `""` | Display name for the project (defaults to `basename`) |
| `region` | `nyc3` | DigitalOcean region for App Platform resources |
| `app_instance_size` | `apps-s-1vcpu-1gb` | App Platform instance size slug |
| `domain` | `evenbreak-cv.businesstime.team` | Domain the droplet serves the chat UI on (its A record is added by hand) |
| `letsencrypt_email` | *(required)* | Contact email for Let's Encrypt. Set it in `deploy/terraform.tfvars` |
| `admin_ssh_public_keys` | *(required)* | SSH public keys allowed to log in to the droplet as root. Terraform registers them as DigitalOcean SSH keys, so none may already be in the account. Set them in `deploy/terraform.tfvars` |
| `droplet_region` | `lon1` | Region for the droplet and its reserved IP |
| `droplet_size` | `s-1vcpu-2gb` | Droplet size (2GB RAM, because Dokku builds the image on the droplet) |
| `droplet_image` | `ubuntu-24-04-x64` | Droplet image |
| `dokku_version` | `v0.38.32` | Dokku release installed by cloud-init |
| `dokku_letsencrypt_version` | `0.25.2` | dokku-letsencrypt plugin release installed by cloud-init |

The app's env is not in `ignore_changes`, so every `terraform apply` redeploys
the App Platform app (the secret always looks like drift). That is accepted while
the app is being retired in favour of the Dokku droplet.

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
| `reserved_ip` | The droplet's reserved IP: the domain's A record and `DOKKU_HOST` |
| `dokku_ssh_known_hosts` | `known_hosts` line for the droplet's SSH host key: the `DOKKU_KNOWN_HOSTS` secret |
| `dokku_deploy_private_key` | *(sensitive)* Deploy key for pushing to Dokku: the `DOKKU_SSH_PRIVATE_KEY` secret |

## Stack details

- **Droplet region**: `lon1`, via the `droplet_region` variable.
- **App Platform region**: Configurable via the `region` variable (default `nyc3`).
- **Inference**: Serverless — no GPU instances to manage.
- **Resource naming**: All resources are suffixed with a random 4-character string to avoid naming collisions.

## Chat UI

The chat UI is a lightweight FastAPI application located in `chat-ui/`. It:

- Serves a single-page web interface with a conversational chat layout.
- Answers each message with app-side retrieval plus serverless inference (see
  Architecture), reading `MODEL_ACCESS_KEY` from the environment.
- Maintains conversation history for multi-turn interactions. The browser holds
  the history, signed with `HISTORY_SIGNING_KEY` so the server rejects history that
  has been edited, added to, reordered or is more than 24 hours old;
  Terraform generates the key (`random_password.history_signing_key`) and passes
  it to the app as a `SECRET` env var, and locally it comes from `.env`.
- Accepts CV uploads (PDF, `.docx`, `.odt`, `.rtf`, `.txt`, `.md`) via
  `POST /api/upload`. The app extracts the document's text server-side
  (`extraction.py`: `pdfplumber` for PDFs, splitting two-column layouts so each
  column reads in order and listing link targets; `python-docx` for Word,
  including tables, headers, footers and text boxes; `striprtf` for RTF),
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
→ **Create model access key** → **Select models**: Qwen3 Embedding 0.6B and the
model-trial models (gpt-oss-20b, DeepSeek V4 Flash, Qwen 3.5 397B, DeepSeek V4 Pro, GLM-5.3 and Kimi K3; see
[Model trial](#model-trial-temporary)) → **No VPC network** → copy the secret (it is shown once) into
`.env` as `MODEL_ACCESS_KEY`.

**3. Run it:**

```bash
docker compose up --build
```

Open <http://localhost:8080>.

The image copies the committed knowledge-base index,
`knowledge-base/index/kb_index.npz`, so builds call no API and need no key.

**Updating the knowledge base.** The index is built from
`knowledge-base/data-sources/` once and committed, so changing the documents (or
the chunking code in `chat-ui/chunking.py`) means rebuilding it. It takes about
a minute and a half and costs about 1¢, using your `.env` key:

```bash
docker compose --profile tools run --rm index
```

Commit `knowledge-base/index/kb_index.npz` in the same change as the documents.
The deploy workflow checks the committed index against the documents first, and
fails without deploying if they don't match. To run that check yourself (no API
calls):

```bash
docker compose --profile tools run --rm index python build_index.py --check --source /kb/data-sources --out /kb/index/kb_index.npz
```

Then rebuild the local image to pick up the new index: `docker compose build chat-ui`.

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
- Chat messages and CV uploads are checked by an app-side guardrail (prompt injection and
  off-topic requests for messages; injection and non-CVs for uploads). A blocked message gets a
  canned reply, and if the check itself fails twice it fails closed and the message is refused.
- `HISTORY_SIGNING_KEY` is generated by Terraform, held in state and injected as a `SECRET`
  env var; rotating it invalidates conversations in progress.
- On the droplet, the model access key and `HISTORY_SIGNING_KEY` are set as Dokku config by
  cloud-init and are also in the droplet's user data and Terraform state. The deploy workflow
  checks the server's host key against the `DOKKU_KNOWN_HOSTS` secret (never `ssh-keyscan`).
- A merge to `main` triggers both App Platform's deploy-on-push and the CI Terraform apply. If the
  push build runs before the apply has added `HISTORY_SIGNING_KEY`, that deployment fails to start,
  App Platform keeps the previous one running, and the apply's redeploy fixes it.
- The chat UI does not store conversation history server-side; all history is held in the browser session.

## Future work

For this first MVP we've reduced the scope in some areas. This is a list of the things
that we think we could come back to at a later date:

- Upgrade the model to one of the Anthropic/OpenAI models in order to use their context
  cache features. This should speed up conversations.
