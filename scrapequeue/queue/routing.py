"""Queue declarations and task routing, driven by config/queues.yml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from kombu import Exchange, Queue

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "queues.yml"


def load_queue_config(path: Path = CONFIG_PATH) -> dict[str, dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    queues = data.get("queues")
    if not isinstance(queues, dict) or not queues:
        raise ValueError(f"{path}: 'queues' must be a non-empty mapping")
    return queues


def build_queues(config: dict[str, dict[str, Any]]) -> tuple[Queue, ...]:
    exchange = Exchange("scrapequeue", type="direct")
    # Redis has no per-queue priority arguments (those are RabbitMQ-only).
    # With the Redis transport, priority is the order in which a worker
    # consumes its queues, so queues are declared highest priority first and
    # the compose/Helm -Q lists follow the same order (see queues_for_worker).
    ordered = sorted(config.items(), key=lambda kv: -int(kv[1].get("priority", 5)))
    return tuple(Queue(name, exchange, routing_key=name) for name, _ in ordered)


def queues_for_worker(names: list[str], config: dict[str, dict[str, Any]] | None = None) -> str:
    """Return a comma-separated -Q value in priority order for the given queues."""
    cfg = config or QUEUE_CONFIG
    unknown = [n for n in names if n not in cfg]
    if unknown:
        raise ValueError(f"unknown queues: {unknown}")
    return ",".join(sorted(names, key=lambda n: -int(cfg[n].get("priority", 5))))


# Explicit routes. Fetcher type decides the queue at dispatch time (see
# pipeline.crawl), so only the fixed-queue tasks are listed here.
TASK_ROUTES: dict[str, dict[str, str]] = {
    "scrapequeue.pipeline.crawl.assemble_job": {"queue": "export"},
    "scrapequeue.pipeline.export.export_job": {"queue": "export"},
    "scrapequeue.api.routes.webhooks.deliver_callback": {"queue": "export"},
    "scrapequeue.queue.deadletter.park": {"queue": "dead_letter"},
}

QUEUE_CONFIG = load_queue_config()
QUEUES = build_queues(QUEUE_CONFIG)
