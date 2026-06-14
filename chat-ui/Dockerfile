# Multi-stage build.
#   base — runtime deps + app source (shared by both targets below)
#   dev  — adds dev requirements (pytest); used by the chat-ui-test compose service
#   prod — the default/final stage App Platform builds (runs uvicorn)
# `prod` is intentionally last so a plain `docker build` (and App Platform) produces
# the production image without needing a --target flag.
FROM python:3.14-slim AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

FROM base AS dev

RUN pip install --no-cache-dir -r requirements-dev.txt
CMD ["python", "-m", "pytest", "-v"]

FROM base AS prod

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
