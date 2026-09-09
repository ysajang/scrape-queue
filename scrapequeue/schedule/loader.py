"""Beat schedule from config/schedule.yml.

The schedule is a file rather than Python so a recurring job can be added or
paused without a code change. RedBeat keeps the schedule in Redis and takes a
lock, so two Beat processes during a rolling deploy do not both fire.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from celery.schedules import crontab

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "schedule.yml"


class ScheduleError(ValueError):
    pass


def _to_schedule(spec: dict[str, Any]) -> Any:
    if "cron" in spec:
        parts = str(spec["cron"]).split()
        if len(parts) != 5:
            raise ScheduleError(f"cron must have five fields, got {spec['cron']!r}")
        minute, hour, day_of_month, month_of_year, day_of_week = parts
        return crontab(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            day_of_week=day_of_week,
        )
    if "every_seconds" in spec:
        return float(spec["every_seconds"])
    raise ScheduleError("each entry needs 'cron' or 'every_seconds'")


def load_schedule(path: Path | None = None) -> dict[str, dict[str, Any]]:
    source = path or CONFIG_PATH
    if not source.is_file():
        return {}
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    entries = data.get("schedules") or {}
    out: dict[str, dict[str, Any]] = {}
    for name, spec in entries.items():
        if not spec.get("enabled", True):
            continue
        job = spec.get("job")
        if not job or "target" not in job:
            raise ScheduleError(f"{name}: 'job.target' is required")
        out[name] = {
            "task": "scrapequeue.schedule.beat.submit_scheduled_job",
            "schedule": _to_schedule(spec),
            "kwargs": {"name": name, "job": job},
            "options": {"queue": "default"},
        }
    return out
