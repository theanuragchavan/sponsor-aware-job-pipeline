# Two stages so the deployed image carries no Node and no build tooling.
#
# The alternative — building the front end on the host's Python runtime — makes
# the deploy depend on whether that runtime happens to ship Node, which is not
# something to find out from a failed build. This is deterministic.

FROM node:22-slim AS ui
WORKDIR /ui
COPY web/ui/package.json web/ui/package-lock.json ./
RUN npm ci
COPY web/ui/ ./
RUN npm run build


FROM python:3.13-slim
WORKDIR /app

COPY requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt

# The pipeline modules the web layer imports, and nothing else. `main.py`,
# `ats_main.py` and the Firecrawl liveness prober are deliberately absent: a
# public demo has no business holding code that can reach gov.uk, an ATS board,
# or a paid scraping API.
COPY tracker.py sponsor_check.py config.py ./
# bootstrap.py puts ./pipeline on sys.path and imports these as top-level
# modules, so there is no package here and no __init__.py to copy.
COPY pipeline/shortlist.py pipeline/sponsor_review.py ./pipeline/
COPY web/__init__.py ./web/
COPY web/api/ ./web/api/
COPY web/demo/data/ ./web/demo/data/
COPY --from=ui /ui/dist ./web/ui/dist

ENV APP_MODE=demo \
    PYTHONUNBUFFERED=1 \
    RATE_LIMIT_PER_MIN=20 \
    MAX_LOG_ENTRIES=5000

EXPOSE 8000
CMD ["sh", "-c", "uvicorn web.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
