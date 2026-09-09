"""FastAPI application: the control plane.

It does three things and nothing else: validate a request, persist a job row,
put a message on the right queue. No fetching happens in this process, so a
slow site can never occupy an API worker.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from scrapequeue.api.routes import health, jobs, results
from scrapequeue.core.settings import get_settings
from scrapequeue.core.targets import list_targets
from scrapequeue.observability.logging import configure_logging, get_logger, request_id_var
from scrapequeue.observability.tracing import configure_tracing, instrument_fastapi

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings, service_name="scrapequeue-api")
    log.info("api.start", environment=settings.environment, targets=list_targets())
    yield
    log.info("api.stop")


app = FastAPI(
    title="scrape-queue",
    version="0.1.0",
    summary="Queued scraping service with retries, checkpoints and per-domain rate limits.",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context(request: Request, call_next):  # noqa: ANN001, ANN201
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    # Never leak a traceback to a caller; the detail goes to the log with the
    # request id so it can still be correlated.
    log.error("api.unhandled", path=request.url.path, error=str(exc), exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "internal error"})


app.include_router(health.router)
app.include_router(jobs.router)
app.include_router(results.router)

instrument_fastapi(app)
