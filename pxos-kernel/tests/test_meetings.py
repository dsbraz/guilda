from datetime import UTC, datetime, timedelta, timezone

from pxos_kernel.meetings import Meeting


def test_attendees_recording_the_same_meeting_name_it_the_same_way() -> None:
    brt = timezone(timedelta(hours=-3))
    one = Meeting("PM@Example.com ", datetime(2026, 11, 16, 9, 0, 0, tzinfo=brt), "Weekly  piloto")
    other = Meeting(
        "pm@example.com", datetime(2026, 11, 16, 12, 0, 30, tzinfo=UTC), "Weekly piloto"
    )
    assert one.key == other.key == "pm@example.com|2026-11-16T12:00Z|Weekly piloto"


def test_another_occurrence_of_a_series_is_another_meeting() -> None:
    monday = Meeting("pm@example.com", datetime(2026, 11, 16, 12, 0, tzinfo=UTC), "Weekly")
    next_monday = Meeting("pm@example.com", datetime(2026, 11, 23, 12, 0, tzinfo=UTC), "Weekly")
    assert monday.key != next_monday.key
