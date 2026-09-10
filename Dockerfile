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
COPY --from=deps /install /usr/local

# The base image keeps its own copies of setuptools and msgpack in the
# distribution's dist-packages directory, which is where a scanner finds the
# vulnerable versions no matter what the application environment holds
# (setuptools CVE-2025-47273, msgpack GHSA-6v7p-g79w-8964). Remove every copy
# on the interpreter's path, then install patched ones.
RUN set -eux; \
    for dir in $(python3 -c "import site; print(' '.join(site.getsitepackages()))"); do \
      rm -rf "$dir"/setuptools "$dir"/setuptools-*.dist-info "$dir"/pkg_resources \
             "$dir"/msgpack "$dir"/msgpack-*.dist-info "$dir"/msgpack-*.egg-info; \
    done; \
    python3 -m pip install --no-cache-dir --upgrade "setuptools>=78.1.1" "msgpack>=1.2.1"

# Fail the build rather than the scan if an old copy survived: a version check
# here names the problem, while a Trivy failure two jobs later does not say
# which directory it came from.
COPY docker/verify_patched.py /tmp/verify_patched.py
RUN python3 /tmp/verify_patched.py && rm -f /tmp/verify_patched.py
COPY scrapequeue ./scrapequeue
COPY targets ./targets
COPY config ./config
COPY alembic.ini ./
# Runs as the image's unprivileged user; compose mounts / read-only and gives
# /tmp and the browser cache as tmpfs.
USER pwuser
EXPOSE 8000
CMD ["uvicorn", "scrapequeue.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
