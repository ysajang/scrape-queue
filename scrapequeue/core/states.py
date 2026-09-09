"""Job and page lifecycle.

Celery has its own task states; these are the states the API exposes and the
database stores. They are deliberately separate: a Celery task can be retried
five times while the job it belongs to stays RUNNING, and a job can be SUSPECT
(finished, output not trustworthy) even though every task returned success.
"""

from __future__ import annotations

from enum import StrEnum


class JobState(StrEnum):
    PENDING = "PENDING"  # accepted, not yet picked up
    RUNNING = "RUNNING"  # at least one page task started
    PAUSED = "PAUSED"  # circuit breaker opened on the target domain
    SUCCEEDED = "SUCCEEDED"  # every page done, output uploaded
    PARTIAL = "PARTIAL"  # output uploaded, some pages permanently failed
    SUSPECT = "SUSPECT"  # completed but drift detector rejected the row counts
    FAILED = "FAILED"  # no usable output
    CANCELLED = "CANCELLED"  # cancelled by the caller
    DEAD_LETTER = "DEAD_LETTER"  # poison pill, parked for an operator


TERMINAL_STATES: frozenset[JobState] = frozenset(
    {
        JobState.SUCCEEDED,
        JobState.PARTIAL,
        JobState.SUSPECT,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.DEAD_LETTER,
    }
)

# Allowed transitions. Anything not listed is a bug, and the repository raises
# rather than silently writing it: a job that goes SUCCEEDED -> RUNNING means
# a duplicate delivery slipped past the checkpoint logic and we want to know.
_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.PENDING: frozenset({JobState.RUNNING, JobState.CANCELLED, JobState.FAILED}),
    JobState.RUNNING: frozenset(
        {
            JobState.RUNNING,
            JobState.PAUSED,
            JobState.SUCCEEDED,
            JobState.PARTIAL,
            JobState.SUSPECT,
            JobState.FAILED,
            JobState.CANCELLED,
            JobState.DEAD_LETTER,
        }
    ),
    JobState.PAUSED: frozenset({JobState.RUNNING, JobState.CANCELLED, JobState.FAILED}),
    # Terminal states may only be re-entered by an explicit retry, which
    # creates a new job row rather than reviving this one.
    JobState.SUCCEEDED: frozenset(),
    JobState.PARTIAL: frozenset(),
    JobState.SUSPECT: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
    JobState.DEAD_LETTER: frozenset(),
}


class IllegalTransition(RuntimeError):
    def __init__(self, current: JobState, target: JobState) -> None:
        super().__init__(f"illegal job transition {current} -> {target}")
        self.current = current
        self.target = target


def can_transition(current: JobState, target: JobState) -> bool:
    return target in _TRANSITIONS[current]


def assert_transition(current: JobState, target: JobState) -> None:
    if not can_transition(current, target):
        raise IllegalTransition(current, target)


class PageState(StrEnum):
    PENDING = "PENDING"
    FETCHED = "FETCHED"  # snapshot stored, not yet parsed
    PARSED = "PARSED"
    FAILED = "FAILED"  # retries exhausted for this page only
