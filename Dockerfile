# Playwright's official image ships Chromium and its system dependencies for
# this exact Playwright version; pinning both together avoids the driver
# mismatch that shows up at runtime as "Executable doesn't exist".
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# git resolves the scrape-kit VCS dependency. python3-venv is not part of the
# Playwright base image: it installs packages into the system interpreter, so
# ensurepip is missing and `python3 -m venv` fails without it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git python3-venv \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

FROM base AS deps
COPY pyproject.toml README.md ./
COPY scrapequeue/__init__.py scrapequeue/__init__.py
# A virtualenv rather than --prefix: on Debian, `pip install --prefix=X` follows
# the posix_local scheme and writes to X/local/lib/..., so copying the tree onto
# /usr/local lands it in /usr/local/local/lib, which is on no interpreter's
# path. A venv has one predictable layout and copies cleanly between stages.
RUN python3 -m venv "$VIRTUAL_ENV" \
 && pip install --upgrade pip "setuptools>=78.1.1" wheel \
 && pip install .

FROM base AS runtime
COPY --from=deps /opt/venv /opt/venv

# The base image ships copies of two packages with current advisories
# (setuptools CVE-2025-47273, msgpack GHSA-6v7p-g79w-8964) outside the venv.
# Install patched versions, then remove every older copy left on disk: they sit
# in directories that are on no sys.path, and a scanner reads all of them. The
# build fails if one survives, so the log names the directory.
RUN python3 -m pip install --no-cache-dir --upgrade "setuptools>=78.1.1" "msgpack>=1.2.1"
COPY docker/verify_patched.py /tmp/verify_patched.py
RUN python3 /tmp/verify_patched.py --purge && rm -f /tmp/verify_patched.py

COPY scrapequeue ./scrapequeue
COPY targets ./targets
COPY config ./config
COPY alembic.ini ./

# Smoke test: a broken install must fail the build, not the first request.
# The placeholder values are only read to satisfy settings validation; nothing
# is contacted at import time.
RUN DATABASE_URL=postgresql+psycopg://u:p@db:5432/db \
    REDIS_URL=redis://redis:6379/0 \
    REDBEAT_REDIS_URL=redis://redis:6379/1 \
    S3_ENDPOINT=http://minio:9000 S3_BUCKET=b S3_ACCESS_KEY=k S3_SECRET_KEY=s \
    python -c "import scrapequeue.api.app, scrapequeue.queue.celery_app; print('imports ok')"

# Runs as the image's unprivileged user; compose mounts the root filesystem
# read-only and provides /tmp and the browser cache as tmpfs.
USER pwuser
EXPOSE 8000
CMD ["uvicorn", "scrapequeue.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
