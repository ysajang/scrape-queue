"""Selector drift detection.

The failure this exists for: a site changes its markup, every selector matches
nothing, every task returns success, and the job produces an empty file that
looks like a legitimate result. Rows-per-page is compared against a rolling
baseline for the target; a run far below it is marked SUSPECT and its output is
kept but flagged, because an empty CSV delivered as success is worse than an
error.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from scrapequeue.core.settings import get_settings
from scrapequeue.db.tables import DriftBaseline


@dataclass(frozen=True)
class DriftVerdict:
    suspect: bool
    observed: float
    baseline: int | None
    reason: str = ""


def check(session: Session, target: str, rows: int, pages: int) -> DriftVerdict:
    if pages <= 0:
        return DriftVerdict(True, 0.0, None, "no pages fetched")
    observed = rows / pages
    baseline = session.get(DriftBaseline, target)
    if baseline is None:
        return DriftVerdict(False, observed, None, "no baseline yet")
    floor = baseline.rows_per_page * get_settings().drift_min_ratio
    if observed < floor:
        return DriftVerdict(
            True,
            observed,
            baseline.rows_per_page,
            f"{observed:.1f} rows/page against a baseline of {baseline.rows_per_page}",
        )
    return DriftVerdict(False, observed, baseline.rows_per_page)


def update_baseline(session: Session, target: str, rows: int, pages: int) -> None:
    """Exponential moving average, so one good run does not overwrite history
    and a slow real decline is still tracked."""
    if pages <= 0:
        return
    observed = rows / pages
    baseline = session.get(DriftBaseline, target)
    if baseline is None:
        session.add(DriftBaseline(target=target, rows_per_page=int(observed), samples=1))
        return
    blended = 0.7 * baseline.rows_per_page + 0.3 * observed
    baseline.rows_per_page = int(blended)
    baseline.samples += 1
