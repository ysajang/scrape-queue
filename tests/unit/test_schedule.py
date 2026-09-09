import textwrap

import pytest

from scrapequeue.schedule.loader import ScheduleError, load_schedule


def write(tmp_path, body: str):
    path = tmp_path / "schedule.yml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_shipped_schedule_is_valid():
    entries = load_schedule()
    assert entries
    assert all("task" in entry for entry in entries.values())


def test_disabled_entries_are_skipped(tmp_path):
    path = write(
        tmp_path,
        """
        schedules:
          off_by_default:
            enabled: false
            cron: "0 * * * *"
            job: {target: federal_register}
        """,
    )
    assert load_schedule(path) == {}


def test_cron_needs_five_fields(tmp_path):
    path = write(
        tmp_path,
        """
        schedules:
          broken:
            cron: "0 *"
            job: {target: federal_register}
        """,
    )
    with pytest.raises(ScheduleError):
        load_schedule(path)


def test_entry_needs_a_target(tmp_path):
    path = write(
        tmp_path,
        """
        schedules:
          broken:
            cron: "0 * * * *"
            job: {}
        """,
    )
    with pytest.raises(ScheduleError):
        load_schedule(path)
