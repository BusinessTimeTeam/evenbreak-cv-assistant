# Multi-stage build. The build context is the REPOSITORY ROOT (so prod can copy
# the committed index, knowledge-base/index/kb_index.npz), with this Dockerfile
# at chat-ui/Dockerfile. The index is built once with build_index.py (the
# compose `index` service) and committed, so image builds call no API and need
# no secrets.
#   deps  — Python + runtime requirements (shared by every stage below)
#   base  — deps + app source
#   dev   — adds dev requirements (pytest); used by the chat-ui-test compose service
#   prod  — the default/final stage Dokku builds (runs uvicorn)
# `prod` is intentionally last so a plain `docker build` (and Dokku) produces
# the production image without needing a --target flag.
FROM python:3.14-slim AS deps

WORKDIR /app

COPY chat-ui/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM deps AS base

COPY chat-ui/ .

FROM base AS dev

RUN pip install --no-cache-dir -r requirements-dev.txt
CMD ["python", "-m", "pytest", "-v"]

FROM base AS prod

COPY knowledge-base/index/kb_index.npz /index/kb_index.npz
ENV INDEX_PATH=/index/kb_index.npz

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
