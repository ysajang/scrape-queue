import pytest

from scrapequeue.core.states import (
    TERMINAL_STATES,
    IllegalTransition,
    JobState,
    assert_transition,
    can_transition,
)


def test_pending_can_start():
    assert can_transition(JobState.PENDING, JobState.RUNNING)


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES))
def test_terminal_states_are_final(terminal):
    """A finished job never goes back to RUNNING. This is what makes a
    redelivered task safe: it can only be a no-op, never a second run."""
    with pytest.raises(IllegalTransition):
        assert_transition(terminal, JobState.RUNNING)


def test_paused_resumes():
    assert can_transition(JobState.PAUSED, JobState.RUNNING)


def test_pending_cannot_jump_to_succeeded():
    with pytest.raises(IllegalTransition):
        assert_transition(JobState.PENDING, JobState.SUCCEEDED)
