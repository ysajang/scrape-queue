# Playwright's official image ships Chromium and its system deps for this exact
# Playwright version; pinning both together avoids the browser/driver mismatch
# that shows up as "Executable doesn't exist" at runtime.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git is needed only to resolve the scrape-kit VCS dependency.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

FROM base AS deps
COPY pyproject.toml README.md ./
COPY scrapequeue/__init__.py scrapequeue/__init__.py
# The Playwright base image ships older build tooling than the current
# advisories allow (setuptools CVE-2025-47273). Upgrading before the install
# means the copy under /install is the patched one.
RUN pip install --prefix=/install --upgrade "setuptools>=78.1.1" "wheel" \
 && pip install --prefix=/install .

FROM base AS runtime
# Patch the interpreter's own site-packages too: the base image's setuptools is
# on PATH regardless of what the application venv contains.
RUN pip install --no-cache-dir --upgrade "setuptools>=78.1.1"
COPY --from=deps /install /usr/local
COPY scrapequeue ./scrapequeue
COPY targets ./targets
COPY config ./config
COPY alembic.ini ./
# Runs as the image's unprivileged user; compose mounts / read-only and gives
# /tmp and the browser cache as tmpfs.
USER pwuser
EXPOSE 8000
CMD ["uvicorn", "scrapequeue.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
