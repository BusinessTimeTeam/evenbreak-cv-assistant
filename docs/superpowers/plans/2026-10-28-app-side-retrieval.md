# App-Side Retrieval and Serverless Inference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the chat UI's call to the DO managed agent with in-app retrieval over `knowledge-base/data-sources/` plus a direct call to DO serverless inference, with the index built automatically in the Docker image, a manual web scraper, and a side-by-side comparison against the old agent.

**Architecture:** A build-time `index` Docker stage chunks the documents, embeds them through DO's Embeddings API and writes one `.npz` index file; the production stage copies only that file. At request time `main.py` rewrites the conversation into search queries, embeds them, does a brute-force cosine search over the in-memory index, and sends system prompt + CV + passages + history to `gpt-oss-20b`. Retrieval sits behind a small `Index` class so storage can be swapped later.

**Tech Stack:** Python 3.14, FastAPI, httpx, numpy, pypdf (existing), trafilatura (scraper only), Docker multi-stage builds, Docker Compose, Terraform (DO provider 2.85), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-28-app-side-retrieval-design.md`

## Global Constraints

- Work on branch `feat/app-side-retrieval` (off `main`). **Do not merge to `main`** — merging deploys; the user merges after approving the comparison.
- Python 3.14, FastAPI, httpx; no vector database and no LangChain-style framework in the app. numpy is the only new app dependency.
- trafilatura must not be in `chat-ui/requirements.txt` (production image).
- Inference base URL default `https://inference.do-ai.run/v1`; auth `Authorization: Bearer $MODEL_ACCESS_KEY`.
- Embedding model default `qwen3-embedding-0.6b`. If it is not callable on the serverless Embeddings API, **stop and ask the user** — do not switch model or embed locally.
- Chat model default: the serverless slug for gpt-oss-20b (confirm from `GET /v1/models`; expected `openai-gpt-oss-20b`). Settings: `temperature=0.2`, `max_tokens=2048`, `top_p=1`.
- Retrieval `k = 10`.
- The CV message keeps the `user` role (`_cv_message` in `chat-ui/main.py`).
- The app refuses to start if the index's embedding model differs from `EMBEDDING_MODEL`.
- The built index is never committed; no real person's CV is ever committed.
- Scraper user agent: `EvenbreakCVAssistant/1.0 (+contact: mail@mayortech.co.uk)`; respect robots.txt; pause between requests.
- Tests stay offline: stub network with `monkeypatch` or `httpx.MockTransport`, following `chat-ui/tests/`.
- **Prerequisite (user action):** a DO model access key in the root `.env` as `MODEL_ACCESS_KEY=…`. Live checks run through the `chat-ui` Compose service, which loads `.env`; the `chat-ui-test` service deliberately does not.

## Review Focus

1. **Rewriter returns junk** (prose, invalid JSON, empty list, or the call errors) → retrieval falls back to the raw latest message; the user still gets an answer. *(Task 4)*
2. **Index file missing or unreadable at startup** (e.g. `uvicorn` run without building the index) → startup fails with a message naming `INDEX_PATH` and the `docker compose build` command, not a bare traceback deep in numpy. *(Task 3)*
3. **Empty documents** (a PDF with no extractable text, an empty `.md`) → skipped with a warning; a build where *every* document is empty fails loudly. *(Task 2)*
4. **Embedding batches come back out of order or short** → vectors must line up with chunks; order by the response's `index` field and raise if the count differs. *(Task 1)*
5. **Inference/embedding API errors mid-chat** (401, 429, 5xx, timeout) → `/api/chat` returns 200 with a readable `Error: …` content string, never a 500. *(Task 5)*

---

### Task 1: Serverless inference client

**Files:**
- Create: `chat-ui/inference.py`
- Create: `chat-ui/tests/test_inference.py`
- Modify: `chat-ui/requirements.txt` (add `numpy`, pinned to a current release)

**Interfaces:**
- Produces:
  - `class InferenceError(Exception)` — any non-2xx, timeout, or malformed response.
  - `@dataclass(frozen=True) class InferenceConfig: api_key: str; base_url: str = "https://inference.do-ai.run/v1"; chat_model: str = "openai-gpt-oss-20b"; embedding_model: str = "qwen3-embedding-0.6b"` with `@classmethod from_env(cls) -> InferenceConfig` reading `MODEL_ACCESS_KEY` (required), `INFERENCE_BASE_URL`, `CHAT_MODEL`, `EMBEDDING_MODEL`.
  - `async def embed(config: InferenceConfig, texts: list[str], *, batch_size: int = 64, client: httpx.AsyncClient | None = None) -> list[list[float]]` — POST `{base_url}/embeddings`, `{"model": config.embedding_model, "input": batch, "encoding_format": "float"}`.
  - `async def chat(config: InferenceConfig, messages: list[dict], *, temperature: float = 0.2, max_tokens: int = 2048, top_p: float = 1.0, model: str | None = None, client: httpx.AsyncClient | None = None) -> str` — POST `{base_url}/chat/completions`, returns `choices[0].message.content`.

- [ ] **Step 1: Write failing tests** in `chat-ui/tests/test_inference.py` using `httpx.MockTransport` passed via `client=httpx.AsyncClient(transport=...)`:
  - `test_embed_sends_model_and_bearer_key` — asserts request URL ends `/embeddings`, header `Authorization == "Bearer k"`, body `model == "qwen3-embedding-0.6b"`.
  - `test_embed_batches_and_preserves_order` — 5 texts, `batch_size=2` → 3 requests; mock returns each batch's `data` **reversed** with correct `index` values; result equals vectors in input order.
  - `test_embed_raises_when_count_mismatches` — mock returns fewer items than inputs → `InferenceError`.
  - `test_chat_returns_first_choice_content` — asserts body contains `temperature == 0.2`, `max_tokens == 2048`, `top_p == 1.0`, returns `"hi"`.
  - `test_chat_raises_inference_error_on_http_error` — mock 429 → `InferenceError` whose message includes `429`.
  - `test_config_from_env_requires_model_access_key` — unset → `KeyError` or `InferenceError`; set → defaults as above.

- [ ] **Step 2: Run to verify they fail**
  Run: `docker compose run --rm chat-ui-test python -m pytest tests/test_inference.py -v`
  Expected: FAIL, `ModuleNotFoundError: No module named 'inference'`.

- [ ] **Step 3: Implement `chat-ui/inference.py`** to the interfaces above. Wrap `httpx.HTTPError` and non-2xx into `InferenceError`; order embedding results by each item's `index`. Timeout 120 s (matches current `_call_agent`). Add `numpy` to `requirements.txt` now (Task 3 needs it; one image rebuild).

- [ ] **Step 4: Run tests** — same command, expected PASS. (Rebuild the test image first if requirements changed: `docker compose build chat-ui-test`.)

- [ ] **Step 5: Live smoke check (real key from `.env`)** — confirms the spec's model assumption before anything depends on it:
  Run: `docker compose run --rm --build chat-ui python -c "import asyncio,inference as i;c=i.InferenceConfig.from_env();print(len(asyncio.run(i.embed(c,['hello']))[0]))"`
  Expected: a vector length (1024 expected for Qwen3 0.6B).
  Run: `docker compose run --rm chat-ui python -c "import httpx,os;r=httpx.get('https://inference.do-ai.run/v1/models',headers={'Authorization':'Bearer '+os.environ['MODEL_ACCESS_KEY']});print([m['id'] for m in r.json()['data'] if 'oss' in m['id'] or 'embed' in m['id']])"`
  Expected: lists the gpt-oss-20b and Qwen3 embedding slugs; fix the `InferenceConfig` defaults if they differ. **If the embedding model is not available, stop and ask the user.**

- [ ] **Step 6: Commit**
  ```bash
  git add chat-ui/inference.py chat-ui/tests/test_inference.py chat-ui/requirements.txt
  git commit -m "feat: Add DO serverless inference client for chat and embeddings"
  ```

---

### Task 2: Document loading and chunking

**Files:**
- Create: `chat-ui/chunking.py`
- Create: `chat-ui/tests/test_chunking.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Chunk: source: str; text: str` — `source` is the path relative to the data-sources root, POSIX style (e.g. `disability-inclusion-course/07-the-models-of-disability.md`).
  - `SUPPORTED_SUFFIXES = (".md", ".txt", ".pdf")`
  - `def load_documents(root: Path) -> list[tuple[str, str]]` — recursive, sorted by relative path; `.pdf` via `pypdf.PdfReader`; logs a warning and skips unsupported suffixes and documents whose stripped text is empty; raises `ValueError("no indexable documents under <root>")` if nothing remains.
  - `def chunk_document(source: str, text: str, *, max_tokens: int = 500, overlap_tokens: int = 50) -> list[Chunk]`
  - `def estimate_tokens(text: str) -> int` — `ceil(len(text) / 4)`.

- [ ] **Step 1: Write failing tests** in `chat-ui/tests/test_chunking.py` (use `tmp_path`; build the PDF with `reportlab`, already a dev dependency):
  - `test_load_documents_reads_md_txt_and_pdf_with_relative_sources` — sources equal `["a.md", "sub/b.txt", "sub/c.pdf"]`.
  - `test_load_documents_skips_unsupported_and_empty_files` — a `.docx`, an empty `.md` and a blank-page PDF are skipped; `caplog` contains a warning naming each.
  - `test_load_documents_raises_when_nothing_indexable` — only empty files → `ValueError` mentioning the root.
  - `test_short_document_is_one_chunk` — 100-word text → one `Chunk` with identical text.
  - `test_chunks_respect_max_tokens` — long text → every `estimate_tokens(c.text) <= 500`.
  - `test_chunks_split_on_markdown_headings_first` — two `## ` sections each under the limit → two chunks, each starting with its heading.
  - `test_consecutive_chunks_overlap` — an oversized paragraph split into windows: the tail of chunk N appears at the start of chunk N+1.
  - `test_no_empty_chunks` — text with runs of blank lines → no chunk whose `text.strip()` is empty.

- [ ] **Step 2: Run to verify they fail**
  Run: `docker compose run --rm chat-ui-test python -m pytest tests/test_chunking.py -v`
  Expected: FAIL, `ModuleNotFoundError: No module named 'chunking'`.

- [ ] **Step 3: Implement `chat-ui/chunking.py`.** Split order: markdown headings (`^#{1,6} `), then blank-line paragraphs, then a sliding character window for any piece still over `max_tokens`, with `overlap_tokens` of overlap. Greedily merge adjacent small pieces up to `max_tokens`. Reuse `extraction._extract_pdf`'s approach (pypdf, join pages with `\n`), but read from a path.

- [ ] **Step 4: Run tests** — same command, expected PASS.

- [ ] **Step 5: Commit**
  ```bash
  git add chat-ui/chunking.py chat-ui/tests/test_chunking.py
  git commit -m "feat: Add knowledge-base document loading and chunking"
  ```

---

### Task 3: Index build, storage, search, and the build CLI

**Files:**
- Create: `chat-ui/retrieval.py`
- Create: `chat-ui/build_index.py`
- Create: `chat-ui/tests/test_retrieval.py`

**Interfaces:**
- Consumes: `Chunk`, `load_documents`, `chunk_document` (Task 2); `InferenceConfig`, `embed` (Task 1).
- Produces:
  - `class IndexLoadError(Exception)` and `class EmbeddingModelMismatch(IndexLoadError)`.
  - `@dataclass(frozen=True) class ScoredChunk: source: str; text: str; score: float`
  - `class Index:`
    - `def __init__(self, chunks: list[Chunk], vectors: np.ndarray, *, embedding_model: str, source_hash: str, built_at: str)` — `vectors` shape `(n, d)` float32, L2-normalised on construction.
    - `def save(self, path: Path) -> None` — single `.npz` (`np.savez_compressed`) holding vectors, chunk sources, chunk texts, and a JSON metadata string `{"embedding_model", "dim", "source_hash", "built_at", "count"}`.
    - `@classmethod def load(cls, path: Path, *, expected_embedding_model: str) -> Index` — raises `IndexLoadError` if missing/unreadable (message names the path and says to run `docker compose build chat-ui`), `EmbeddingModelMismatch` if the model differs (message names both models).
    - `def search(self, query_vectors: Sequence[Sequence[float]], k: int = 10) -> list[ScoredChunk]` — cosine over all chunks for each query; merge by best score per chunk (de-duplicated); return top `k` by score descending.
    - properties `embedding_model: str`, `source_hash: str`, `__len__`.
  - `def source_hash(documents: list[tuple[str, str]]) -> str` — sha256 over sorted `(source, text)` pairs.
  - `async def build_index(root: Path, config: InferenceConfig) -> Index`
  - `build_index.py` CLI: `python build_index.py --source DIR --out FILE`; reads `InferenceConfig.from_env()`; prints chunk count, dimension and elapsed time; non-zero exit on any error.

- [ ] **Step 1: Write failing tests** in `chat-ui/tests/test_retrieval.py` with hand-made 3-d vectors and a stubbed `embed` (monkeypatch `retrieval.embed`):
  - `test_search_returns_most_similar_first` — vectors `[1,0,0]`, `[0,1,0]`, `[0.9,0.1,0]`; query `[1,0,0]`, `k=2` → sources in order `["a", "c"]`.
  - `test_search_merges_multiple_queries_without_duplicates` — two queries both best-matching chunk `a` → `a` appears once, with the higher of its two scores.
  - `test_search_k_larger_than_index_returns_all` — 3 chunks, `k=10` → 3 results.
  - `test_save_and_load_round_trip` — `tmp_path / "kb_index.npz"`; loaded index has the same `len`, `embedding_model`, `source_hash`, and identical search results.
  - `test_load_rejects_different_embedding_model` — saved with `"qwen3-embedding-0.6b"`, loaded expecting `"bge-m3"` → `EmbeddingModelMismatch`; message contains both names.
  - `test_load_missing_file_raises_helpful_error` — `IndexLoadError`; message contains the path and `docker compose build`.
  - `test_build_index_embeds_every_chunk_and_records_metadata` — `tmp_path` with two small `.md` files; stub `embed` returns one vector per text; resulting `len(index)` equals total chunk count and `index.embedding_model == config.embedding_model`.
  - `test_source_hash_changes_when_a_document_changes`.

- [ ] **Step 2: Run to verify they fail**
  Run: `docker compose run --rm chat-ui-test python -m pytest tests/test_retrieval.py -v`
  Expected: FAIL, `ModuleNotFoundError: No module named 'retrieval'`.

- [ ] **Step 3: Implement `chat-ui/retrieval.py` and `chat-ui/build_index.py`** to the interfaces. Embed `chunk.text` prefixed with its source path on one line (`f"{source}\n{text}"`) so the file name contributes to matching; store the unprefixed text. Store sources/texts as numpy unicode arrays so `np.load(..., allow_pickle=False)` works.

- [ ] **Step 4: Run tests** — same command, expected PASS.

- [ ] **Step 5: Live build against the real corpus**
  Run: `docker compose run --rm -v "$PWD/knowledge-base/data-sources:/kb:ro" -v "$PWD/.index:/index" chat-ui python build_index.py --source /kb --out /index/kb_index.npz`
  Expected: completes in under ~2 minutes, prints roughly 600–1,500 chunks and dim 1024. Add `.index/` to `.gitignore` in this commit.

- [ ] **Step 6: Commit**
  ```bash
  git add chat-ui/retrieval.py chat-ui/build_index.py chat-ui/tests/test_retrieval.py .gitignore
  git commit -m "feat: Build, store and search an in-memory knowledge-base index"
  ```

---

### Task 4: System prompt, query rewriting, and message assembly

**Files:**
- Create: `chat-ui/prompts/system.md` — the `agent_instruction` text from `deploy/terraform.tfvars:8-27`, verbatim (without the heredoc indentation).
- Create: `chat-ui/prompting.py`
- Create: `chat-ui/tests/test_prompting.py`

**Interfaces:**
- Consumes: `ScoredChunk` (Task 3), `chat`, `InferenceConfig`, `InferenceError` (Task 1).
- Produces:
  - `def load_system_prompt(path: Path = Path(__file__).parent / "prompts" / "system.md") -> str`
  - `MAX_QUERIES = 4`
  - `async def rewrite_queries(config: InferenceConfig, message: str, history: list[dict], cv_text: str | None) -> list[str]` — one `chat(...)` call (temperature 0) asking for a JSON array of up to `MAX_QUERIES` standalone search queries using the last 6 history turns and the first ~2,000 characters of the CV. Returns `[message]` on `InferenceError`, unparseable output, a non-list, or an empty list; strips blanks; caps at `MAX_QUERIES`. Tolerates the array being wrapped in prose or a ```json fence.
  - `def context_message(passages: list[ScoredChunk]) -> dict` — one `user`-role message; each passage delimited and headed with its source file name; introduced as knowledge-base context.
  - `def build_messages(system_prompt: str, cv_message: dict | None, passages: list[ScoredChunk], history: list[dict], message: str) -> list[dict]` — order: `system`, CV message (if any), context message (if any passages), history turns, final `user` message.

- [ ] **Step 1: Write failing tests** in `chat-ui/tests/test_prompting.py` (stub `prompting.chat` with monkeypatch):
  - `test_system_prompt_file_matches_current_agent_instruction` — contains `"You are Evenbreak's UK-focused CV, employment and disability-inclusion assistant."` and `"Equality Act 2010"`.
  - `test_rewrite_parses_json_array` — stub returns `'["reasonable adjustments interview", "disclosing disability on CV"]'` → that list.
  - `test_rewrite_extracts_array_from_fenced_or_prose_output` — stub returns `'Here you go:\n```json\n["a","b"]\n```'` → `["a", "b"]`.
  - `test_rewrite_falls_back_to_message_on_junk` — parametrised over `"not json"`, `"[]"`, `'{"q": "x"}'` → `[message]`.
  - `test_rewrite_falls_back_to_message_on_inference_error` — stub raises `InferenceError` → `[message]`.
  - `test_rewrite_caps_query_count` — stub returns 7 queries → 4.
  - `test_rewrite_prompt_includes_history_and_cv` — captured messages mention the last history turn's content and a CV snippet.
  - `test_build_messages_order` — roles/positions: `[system, cv, context, *history, user]`; with no CV and no passages → `[system, *history, user]`.
  - `test_context_message_labels_each_passage_with_source` — both source names appear in the content, in score order.

- [ ] **Step 2: Run to verify they fail**
  Run: `docker compose run --rm chat-ui-test python -m pytest tests/test_prompting.py -v`
  Expected: FAIL, `ModuleNotFoundError: No module named 'prompting'`.

- [ ] **Step 3: Implement `chat-ui/prompting.py` and create `chat-ui/prompts/system.md`.**

- [ ] **Step 4: Run tests** — same command, expected PASS.

- [ ] **Step 5: Commit**
  ```bash
  git add chat-ui/prompting.py chat-ui/prompts/system.md chat-ui/tests/test_prompting.py
  git commit -m "feat: Add system prompt, query rewriting and message assembly"
  ```

---

### Task 5: Wire the pipeline into the chat endpoint

**Files:**
- Modify: `chat-ui/main.py` (module docstring; env vars at lines 35–36; `_call_agent` at 79–100; `chat` at 103–128)
- Modify: `chat-ui/tests/test_upload.py` (fixture at 23–37 and env setup at 13–15)
- Create: `chat-ui/tests/test_chat_pipeline.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces:
  - Env: `MODEL_ACCESS_KEY` (required), `INFERENCE_BASE_URL`, `CHAT_MODEL`, `EMBEDDING_MODEL`, `INDEX_PATH` (default `/index/kb_index.npz`), `RETRIEVAL_K` (default `10`). `AGENT_ENDPOINT` / `AGENT_API_KEY` are no longer read.
  - The index is loaded once in a FastAPI lifespan handler into a module-level `_index: Index | None`; a load failure aborts startup with the `IndexLoadError` message.
  - `async def _answer(message: str, history: list[dict], cv: dict | None) -> tuple[str, list[ScoredChunk]]` — rewrite → `embed` queries → `_index.search(k=RETRIEVAL_K)` → `build_messages` → `chat`. Tests stub this or its parts.
  - `/api/chat` response becomes `{"content": str, "sources": [{"source": str, "score": float}]}`. The browser UI ignores `sources`; the comparison script uses it.
  - Any `InferenceError` → `{"content": "Error: <message>", "sources": []}` with status 200, matching today's `_call_agent` error style.

- [ ] **Step 1: Update `test_upload.py`** to the new seams: set `MODEL_ACCESS_KEY` instead of the agent vars; the fixture stubs `main._answer` (capturing `message`, `history`, `cv`) instead of `_call_agent`, and sets `main._index` to a tiny in-memory `Index`. Keep every existing test's intent: e.g. `test_chat_injects_stored_cv_as_leading_message` now asserts the stored CV dict reaches `_answer`, and add an assertion in `test_chat_pipeline.py` that the assembled messages put `_cv_message(cv)` right after the system message.

- [ ] **Step 2: Write failing tests** in `chat-ui/tests/test_chat_pipeline.py` (stub `main.embed`, `main.chat`, `main.rewrite_queries`; real small `Index`):
  - `test_chat_returns_content_and_sources` — response JSON has `content == "stubbed answer"` and `sources[0]["source"]` equals the best-matching chunk.
  - `test_chat_sends_system_prompt_cv_and_context_to_model` — captured `chat` messages: `[0]["role"] == "system"`, CV text present, the retrieved chunk text present, final message is the user's.
  - `test_chat_embeds_every_rewritten_query` — rewrite stub returns 3 queries → `embed` called with those 3 texts.
  - `test_chat_inference_error_returns_readable_error` — `chat` stub raises `InferenceError("429 Too Many Requests")` → status 200, `content` starts `"Error:"`, `sources == []`.
  - `test_chat_embedding_error_returns_readable_error` — same for `embed`.
  - `test_startup_fails_with_helpful_message_when_index_missing` — `INDEX_PATH` to a nonexistent file; entering `TestClient(main.app)` as a context manager raises with a message containing the path.

- [ ] **Step 3: Run to verify they fail**
  Run: `docker compose run --rm chat-ui-test python -m pytest -v`
  Expected: the new tests FAIL; updated `test_upload.py` tests FAIL on the missing `_answer`.

- [ ] **Step 4: Implement the changes in `chat-ui/main.py`.** Remove `_call_agent`. Update the module docstring to describe the new env vars and flow. Keep `_cv_message` unchanged.

- [ ] **Step 5: Run the full suite** — same command, expected all PASS.

- [ ] **Step 6: Commit**
  ```bash
  git add chat-ui/main.py chat-ui/tests/
  git commit -m "feat: Answer chats with app-side retrieval and serverless inference"
  ```

---

### Task 6: Docker index stage, Compose, and local development docs

**Files:**
- Modify: `chat-ui/Dockerfile`
- Create: `.dockerignore` (repo root — the build context moves to the root)
- Modify: `docker-compose.yml`
- Modify: `README.md` (local development section)
- Modify: `.env.example` (add `MODEL_ACCESS_KEY=`; keep `AGENT_ENDPOINT` / `AGENT_API_KEY`, now commented as used only by `evals/compare.py`)

**Interfaces:**
- Produces: image path `/index/kb_index.npz` in the `prod` stage; build arg `MODEL_ACCESS_KEY`; Compose services `chat-ui` and `chat-ui-test` building from context `.` with `dockerfile: chat-ui/Dockerfile`.

- [ ] **Step 1: Restructure `chat-ui/Dockerfile`** (paths now relative to the repo root):
  - `deps` — `python:3.14-slim`, `WORKDIR /app`, copy `chat-ui/requirements.txt`, `pip install`.
  - `index` — `FROM deps`; `ARG MODEL_ACCESS_KEY`; `ARG EMBEDDING_MODEL=qwen3-embedding-0.6b`; copy **only** `chat-ui/inference.py chat-ui/chunking.py chat-ui/retrieval.py chat-ui/build_index.py` and `knowledge-base/data-sources/` → `/kb`; `RUN test -n "$MODEL_ACCESS_KEY"` with a clear error, then `python build_index.py --source /kb --out /index/kb_index.npz`. Copying only these files means editing `main.py` or `static/` never re-embeds.
  - `base` — `FROM deps`, `COPY chat-ui/ .`.
  - `dev` — as today (no index; tests don't need it).
  - `prod` — `FROM base`, `COPY --from=index /index /index`, `ENV INDEX_PATH=/index/kb_index.npz`, keep `EXPOSE 8080` and the uvicorn `CMD`. Must stay the last stage.

- [ ] **Step 2: Create `.dockerignore`** excluding at least `.git`, `.env`, `env.sh`, `deploy/`, `docs/`, `.index/`, `**/__pycache__`, `scratch.txt`, `tools/scrape/input/`, `evals/results/`. Verify secrets are excluded:
  Run: `docker build -f chat-ui/Dockerfile --target deps -t ctx-check . && docker run --rm ctx-check ls -a /app`
  Expected: no `.env` (only what `deps` copies — confirms nothing leaks via context).

- [ ] **Step 3: Update `docker-compose.yml`:** both services `build: {context: ., dockerfile: chat-ui/Dockerfile, target: …}`; `chat-ui` gets `args: {MODEL_ACCESS_KEY: ${MODEL_ACCESS_KEY}}` (Compose reads the root `.env` for interpolation); keep `./chat-ui:/app` mount and `--reload` (the index lives at `/index`, outside the mount). Update the header comment: it no longer talks to the managed agent.

- [ ] **Step 4: Verify the full local flow with the real key**
  Run: `docker compose up --build -d chat-ui && sleep 5 && curl -s localhost:8080/health && curl -s -X POST localhost:8080/api/chat -H 'Content-Type: application/json' -d '{"message":"How should I mention a gap in my CV caused by illness?","history":[]}' | jq '{content: .content[0:300], sources}'`
  Expected: `{"status":"ok"}`, then an answer plus a non-empty `sources` list naming knowledge-base files.
  Then edit a comment in `chat-ui/main.py`, run `docker compose build chat-ui`, and confirm the `index` stage shows `CACHED`. Touch a file in `knowledge-base/data-sources/` and confirm it rebuilds the index.

- [ ] **Step 5: Update `README.md`** local development section: `MODEL_ACCESS_KEY` in `.env`; `docker compose up --build` builds the index; `docker compose build chat-ui` forces a rebuild after document edits; the index is never committed. Keep the "Known issues" sections (still true until step 4).

- [ ] **Step 6: Run the test suite** — `docker compose run --rm chat-ui-test python -m pytest -v`, expected all PASS.

- [ ] **Step 7: Commit**
  ```bash
  git add chat-ui/Dockerfile .dockerignore docker-compose.yml README.md .env.example
  git commit -m "build: Build the knowledge-base index in a Docker stage"
  ```

---

### Task 7: Scraper and the homepage document

**Files:**
- Create: `tools/scrape/scrape.py`, `tools/scrape/requirements.txt` (`trafilatura`, pinned), `tools/scrape/Dockerfile`
- Create: `tools/scrape/tests/test_scrape.py`, `tools/scrape/tests/fixtures/sample.html` (hand-written: nav, cookie banner, footer, and an `<article>` with a heading and two paragraphs)
- Create: `knowledge-base/data-sources/web/www-evenbreak-com.md`
- Modify: `docker-compose.yml` (add `scrape` service, profile `tools`), `.gitignore` (`tools/scrape/input/`), `README.md` (how to scrape)

**Interfaces:**
- Produces:
  - `USER_AGENT = "EvenbreakCVAssistant/1.0 (+contact: mail@mayortech.co.uk)"`
  - `def extract_markdown(html: str, url: str) -> str | None` — trafilatura `extract(..., output_format="markdown", include_links=False, include_tables=True)`.
  - `def output_filename(url: str) -> str` — host + path, lowercased, non-alphanumerics → `-`, collapsed and trimmed, `.md` suffix: `https://www.evenbreak.com/` → `www-evenbreak-com.md`; `https://www.evenbreak.com/article/why-x/` → `www-evenbreak-com-article-why-x.md`.
  - `def render_document(url: str, markdown: str, captured: date) -> str` — first line `> Source: <url> — captured <YYYY-MM-DD>`, blank line, then the markdown.
  - CLI: `python scrape.py URL [URL ...]`, `python scrape.py --sitemap SITEMAP_URL`, `python scrape.py --html FILE --url ORIGINAL_URL`; `--out DIR` (default `/out`), `--delay SECONDS` (default `2`). URL mode checks robots.txt (`urllib.robotparser`, same user agent) and skips disallowed URLs with a message; an HTTP 403/other failure prints the status and the hint to ask the site owner to whitelist `USER_AGENT`, continues with the remaining URLs, and exits non-zero at the end if any failed.
  - Compose `scrape` service: builds `tools/scrape/Dockerfile`, mounts `./knowledge-base/data-sources/web:/out` and `./tools/scrape/input:/input:ro`; usage `docker compose run --rm scrape --html /input/home.htm --url https://www.evenbreak.com/`.

- [ ] **Step 1: Write failing tests** in `tools/scrape/tests/test_scrape.py`:
  - `test_extract_keeps_article_and_drops_boilerplate` — fixture → contains the article heading and both paragraphs; does not contain the nav, cookie-banner or footer text.
  - `test_output_filename` — the two examples above.
  - `test_render_document_header` — first line equals `"> Source: https://www.evenbreak.com/ — captured 2026-10-28"`.
  - `test_fetch_uses_honest_user_agent_and_reports_403` — monkeypatch the fetch function to capture headers/config and return status 403 → the run's result records a failure mentioning `403` and `USER_AGENT`; asserts the configured UA equals `USER_AGENT`.
  - `test_robots_disallowed_url_is_skipped` — stub robot parser disallowing → no fetch call made.

- [ ] **Step 2: Run to verify they fail**
  Run: `docker compose run --rm --entrypoint python scrape -m pytest tests -v`
  Expected: FAIL, `ModuleNotFoundError: No module named 'scrape'`. (Dockerfile: `python:3.14-slim`, install `requirements.txt` plus `pytest`, `WORKDIR /app`, copy `tools/scrape/`, `ENTRYPOINT ["python", "scrape.py"]`.)

- [ ] **Step 3: Implement `tools/scrape/scrape.py`**, the Dockerfile, and the Compose service.

- [ ] **Step 4: Run tests** — same command, expected PASS.

- [ ] **Step 5: Generate the homepage document from the user's saved copy**
  Run: `mkdir -p tools/scrape/input && cp ~/"Downloads/Evenbreak - Award-winning Job board for disabled people.htm" tools/scrape/input/home.htm && docker compose run --rm scrape --html /input/home.htm --url https://www.evenbreak.com/`
  Expected: `knowledge-base/data-sources/web/www-evenbreak-com.md` exists, starts with the source header, ~374 words, includes the "Browse jobs by" lists **unchanged** (kept as-is for the comparison), and has no navigation/cookie/footer text. Do not commit the saved `.htm`.

- [ ] **Step 6: Update `README.md`** with a short "Adding web pages to the knowledge base" section (URL, sitemap, and saved-HTML modes; the 403 / whitelist note).

- [ ] **Step 7: Commit**
  ```bash
  git add tools/scrape docker-compose.yml .gitignore README.md knowledge-base/data-sources/web/www-evenbreak-com.md
  git commit -m "feat: Add trafilatura scraper and capture the Evenbreak homepage"
  ```

---

### Task 8: Draft the comparison test set — **user review checkpoint**

**Files:**
- Create: `evals/cases.json`
- Create: `evals/cvs/cv-career-changer.md`, `evals/cvs/cv-employment-gap.md`, `evals/cvs/cv-graduate.md` (invented people; clearly fictional names, e.g. "Alex Example"; no real employers' internal details)

**Interfaces:**
- Produces: `evals/cases.json` — a list of `{"id": str, "cv": str | null (file name under evals/cvs/), "turns": [str, ...], "notes": str}`. `notes` says what a good answer should draw on (e.g. "course module on reasonable adjustments; Access to Work").

- [ ] **Step 1: Draft 12–15 cases** grounded in topics that exist in `knowledge-base/data-sources/` (check file names/headings before writing each): reasonable adjustments at interview, whether/how to disclose a disability on a CV, explaining an employment gap due to illness, inclusive wording, neurodivergence and strengths-based framing, chronic health and flexible working, Access to Work, the social model of disability, self-advocacy, Northern Ireland vs Great Britain law (checks the system prompt), plus 2–3 **multi-turn** cases whose second turn is a follow-up that only makes sense with history (e.g. "And how would I word that for my second job?"). At least 5 cases use a CV.

- [ ] **Step 2: Validate the file**
  Run: `python3 -c "import json,pathlib;c=json.load(open('evals/cases.json'));assert 12<=len(c)<=15;assert all((pathlib.Path('evals/cvs')/x['cv']).exists() for x in c if x['cv']);assert sum(len(x['turns'])>1 for x in c)>=2;print(len(c),'cases ok')"`
  Expected: `N cases ok`.

- [ ] **Step 3: Commit**
  ```bash
  git add evals/cases.json evals/cvs
  git commit -m "test: Draft comparison cases and invented CVs for review"
  ```

- [ ] **Step 4: STOP — ask the user to review `evals/cases.json` and `evals/cvs/`.** Apply their edits and commit before Task 9.

---

### Task 9: Comparison script, baseline, and report — **user review checkpoint**

**Files:**
- Create: `evals/compare.py`, `evals/tests/test_compare.py`
- Create (generated, committed): `evals/baseline/old-agent.json`
- Create (generated, gitignored): `evals/results/` — add to `.gitignore`
- Modify: `docker-compose.yml` (add `evals` service, profile `tools`, dev image, mounts `./evals:/evals` and `./chat-ui:/app`, env from `.env`)

**Interfaces:**
- Consumes: `main._cv_message` (Task 5 kept it unchanged), `extraction` (existing); new app's `/api/chat` and `/api/upload` (Task 5) over HTTP at `http://chat-ui:8080`.
- Produces:
  - `async def run_old(case: dict, cvs_dir: Path) -> list[dict]` — calls `AGENT_ENDPOINT` with `AGENT_API_KEY`, building messages exactly as the old app did: `_cv_message({"filename", "text"})` first when the case has a CV, then history, then the turn. Each turn's answer is fed into the next turn's history. Returns `[{"turn": str, "answer": str}]`.
  - `async def run_new(case: dict, cvs_dir: Path, base_url: str) -> list[dict]` — uploads the CV (as a `.docx` built with python-docx from the markdown's lines) to get a `session_id`, then posts each turn to `/api/chat` with its own running history. Returns `[{"turn", "answer", "sources": [...]}]`.
  - `def render_report(cases, old: dict, new: dict) -> str` — markdown: per case a heading, the CV name, then for each turn the question, **Old agent**, **New pipeline**, and the new pipeline's source file list.
  - CLI: `python compare.py --baseline` (run old only, write `evals/baseline/old-agent.json`); `python compare.py` (run new, read the baseline, write `evals/results/<timestamp>/new.json` and `report.md`); `--case ID` to run one case.

- [ ] **Step 1: Write failing tests** in `evals/tests/test_compare.py` (`httpx.MockTransport` for both endpoints):
  - `test_run_old_puts_cv_message_first_and_threads_history` — 2-turn case with CV: second request's messages are `[cv_message, user t1, assistant a1, user t2]`.
  - `test_run_new_uploads_cv_once_and_reuses_session` — one upload call; both chat calls carry the same `session_id`; second carries history.
  - `test_render_report_shows_both_answers_and_sources_per_turn`.

- [ ] **Step 2: Run to verify they fail**
  Run: `docker compose run --rm evals python -m pytest /evals/tests -v`
  Expected: FAIL, module not found.

- [ ] **Step 3: Implement `evals/compare.py`** and the Compose service.

- [ ] **Step 4: Run tests** — same command, expected PASS.

- [ ] **Step 5: Capture the baseline from the live old agent** (requires `AGENT_ENDPOINT` / `AGENT_API_KEY` in `.env`)
  Run: `docker compose run --rm evals python /evals/compare.py --baseline`
  Expected: `evals/baseline/old-agent.json` with an answer for every turn of every case; spot-check that none is an `Error:` string (a guardrail refusal is a valid answer to record).

- [ ] **Step 6: Run the new pipeline and produce the report**
  Run: `docker compose up -d --build chat-ui && docker compose run --rm evals python /evals/compare.py`
  Expected: `evals/results/<timestamp>/report.md` covering every case.

- [ ] **Step 7: Commit the script and baseline**
  ```bash
  git add evals/compare.py evals/tests evals/baseline docker-compose.yml .gitignore
  git commit -m "test: Add old-vs-new comparison script and old agent baseline"
  ```

- [ ] **Step 8: STOP — give the user the report path and ask them to judge it.** Note anything that looks like weak retrieval (wrong or irrelevant sources). Tuning (chunk size, k, rewrite prompt) happens here only if the user asks for it.

---

### Task 10: Make step 1 deployable (Terraform and CI) — no merge

**Files:**
- Modify: `deploy/app.tf` (`lifecycle` at 12–14; `source_dir`/`dockerfile_path`; env blocks at 50–61)
- Modify: `deploy/variables.tf` (add `model_access_key`)
- Modify: `.github/workflows/terraform-apply.yml` (env block)
- Modify: `README.md` (deployment notes)

**Interfaces:**
- Produces: `variable "model_access_key"` (`type = string`, `sensitive = true`, no default); GitHub secret `MODEL_ACCESS_KEY` → `TF_VAR_model_access_key`.

- [ ] **Step 1: Edit `deploy/app.tf`:**
  - remove the `lifecycle { ignore_changes = [spec[0].service[0].env] }` block and its comment
  - `source_dir = "/"`, `dockerfile_path = "chat-ui/Dockerfile"`
  - replace the `AGENT_ENDPOINT` / `AGENT_API_KEY` env entries with `MODEL_ACCESS_KEY` (`scope = "RUN_AND_BUILD_TIME"`, `type = "SECRET"`, value `var.model_access_key`)
  - remove `depends_on = [digitalocean_gradientai_agent.rag_agent]`
  - leave the agent, knowledge base, guardrails, Spaces and `agent_api_key` variable in place — step 4 removes them, and the comparison may still need the agent.

- [ ] **Step 2: Add `TF_VAR_model_access_key: ${{ secrets.MODEL_ACCESS_KEY }}` to the workflow env.** Keep `TF_VAR_agent_api_key` (the variable still exists).

- [ ] **Step 3: Validate**
  Run: `cd deploy && terraform fmt -check && terraform init -backend=false -input=false >/dev/null && terraform validate`
  Expected: `Success! The configuration is valid.`

- [ ] **Step 4: Update `README.md`** deployment notes: create a model access key, add it as the `MODEL_ACCESS_KEY` GitHub secret **before merging**, and expect one app redeploy per apply now that env drift isn't ignored.

- [ ] **Step 5: Commit**
  ```bash
  git add deploy/app.tf deploy/variables.tf .github/workflows/terraform-apply.yml README.md
  git commit -m "deploy: Build chat-ui from repo root with a model access key"
  ```

- [ ] **Step 6: STOP — do not merge or push to `main`.** Tell the user the branch is ready, list the pre-merge checklist (comparison approved; `MODEL_ACCESS_KEY` GitHub secret set; after the first deploy, check the App Platform build log shows the `index` stage running), and let them merge.
