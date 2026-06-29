from datetime import datetime, timezone

from pd_shift.timefmt import format_duration, format_trigger_time, parse_pd_timestamp


def test_format_trigger_time_hours():
    now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    created = "2026-06-29T08:00:00Z"
    assert format_trigger_time(created, now=now) == "4h ago"


def test_format_trigger_time_days():
    now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    created = "2026-06-27T12:00:00Z"
    assert format_trigger_time(created, now=now) == "2d ago"


def test_format_trigger_time_week_shows_date():
    now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    created = "2026-06-15T10:00:00Z"
    assert format_trigger_time(created, now=now) == "2026-06-15"


def test_format_trigger_time_missing():
    assert format_trigger_time(None) == "—"


def test_format_duration_resolved():
    assert format_duration("2026-06-27T08:00:00Z", "2026-06-27T10:30:00Z") == "2h 30m"


def test_format_duration_open():
    now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    assert (
        format_duration("2026-06-29T08:00:00Z", None, now=now)
        == "open (4h)"
    )


def test_parse_pd_timestamp_zulu():
    dt = parse_pd_timestamp("2026-06-29T08:00:00Z")
    assert dt.utcoffset() is not None
