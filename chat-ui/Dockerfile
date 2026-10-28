# Multi-stage build. The build context is the REPOSITORY ROOT (so the index
# stage can read knowledge-base/), with this Dockerfile at chat-ui/Dockerfile.
#   deps  — Python + runtime requirements (shared by every stage below)
#   index — embeds knowledge-base/data-sources/ into /index/kb_index.npz
#   base  — deps + app source
#   dev   — adds dev requirements (pytest); used by the chat-ui-test compose service
#   prod  — the default/final stage App Platform builds (runs uvicorn)
# `prod` is intentionally last so a plain `docker build` (and App Platform) produces
# the production image without needing a --target flag.
FROM python:3.14-slim AS deps

WORKDIR /app

COPY chat-ui/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Build the knowledge-base index. Only the modules the build needs are copied,
# so editing main.py or static/ reuses the cached index instead of re-embedding;
# it re-runs when a document (or one of these modules) changes.
# MODEL_ACCESS_KEY arrives as a build arg (App Platform passes build-time env
# vars this way). It exists only in this stage: prod copies just the index file.
# Build args are exported to RUN's environment, and build_index.py reads them
# from there. Never reference $MODEL_ACCESS_KEY in the RUN line itself: build
# output prints RUN commands with build args expanded, which would put the key
# in the build log.
FROM deps AS index

ARG MODEL_ACCESS_KEY
ARG EMBEDDING_MODEL=qwen3-embedding-0.6b
ARG INFERENCE_BASE_URL=https://inference.do-ai.run/v1

COPY chat-ui/inference.py chat-ui/chunking.py chat-ui/retrieval.py chat-ui/build_index.py ./
COPY knowledge-base/data-sources/ /kb/

RUN python build_index.py --source /kb --out /index/kb_index.npz

FROM deps AS base

COPY chat-ui/ .

FROM base AS dev

RUN pip install --no-cache-dir -r requirements-dev.txt
CMD ["python", "-m", "pytest", "-v"]

FROM base AS prod

COPY --from=index /index /index
ENV INDEX_PATH=/index/kb_index.npz

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
