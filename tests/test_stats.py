from datetime import datetime, timedelta, timezone

from pd_shift.parse import alert_signature, incident_matches_signature
from pd_shift.stats import (
    StatsRow,
    aggregate_problem_candidates,
    build_stats_rows,
    clean_note,
    collect_notes,
    collect_prb_candidates,
    dominant_fire_minute,
    is_typical_fire_time,
    print_inc_not_open_hint,
    prompt_inc_history_search,
    render_stats,
    summarize_stats,
    typical_fire,
)


SAMPLE_TITLE = (
    "Percona_MS_SampleAlertRule - CRITICAL - MySQL Threads Running - db1"
)


def test_alert_signature():
    customer, signature = alert_signature(SAMPLE_TITLE, "Zephyr Labs - Gascan")
    assert customer == "Zephyr Labs"
    assert signature == "MySQL Threads Running - db1"


def test_incident_matches_signature():
    incident = {
        "title": SAMPLE_TITLE,
        "service": {"summary": "Zephyr Labs - Gascan"},
    }
    customer, signature = alert_signature(SAMPLE_TITLE, "Zephyr Labs - Gascan")
    assert incident_matches_signature(incident, customer, signature)


def test_incident_matches_same_description_different_customer():
    incident = {
        "title": SAMPLE_TITLE,
        "service": {"summary": "Alpha Co - Gascan"},
    }
    customer, signature = alert_signature(SAMPLE_TITLE, "Zephyr Labs - Gascan")
    assert incident_matches_signature(incident, customer, signature)


def test_clean_note_strips_servicenow_url():
    note = (
        "Checked threads. See https://percona.service-now.com/incident.do?sys_id=abc "
        "for details."
    )
    assert clean_note(note) == "Checked threads. See for details."


def test_collect_notes_deduplicates():
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    times = [base.replace(hour=3), base.replace(hour=4), base.replace(hour=22)]
    fire_range, count, start = typical_fire(times, window_hours=3)
    assert fire_range == "03:00–06:00 UTC"
    assert count == 2
    assert start == 3


def test_is_typical_fire_time():
    base = datetime(2026, 6, 14, 8, 32, tzinfo=timezone.utc)
    assert is_typical_fire_time(base, start_hour=7, window_hours=3)
    assert not is_typical_fire_time(
        base.replace(hour=13, minute=18),
        start_hour=7,
        window_hours=3,
    )


def test_print_inc_not_open_hint(capsys):
    from io import StringIO

    from rich.console import Console

    out = Console(file=StringIO(), width=120, highlight=False)
    print_inc_not_open_hint(out, "INC0011223")
    text = out.file.getvalue()
    assert "INC0011223 is not in open incidents" in text
    assert "pd stats 123456" in text


def test_prompt_inc_history_search_defaults_to_no(monkeypatch):
    from io import StringIO

    from rich.console import Console

    out = Console(file=StringIO(), width=120, highlight=False)
    monkeypatch.setattr("click.confirm", lambda *args, **kwargs: False)
    assert prompt_inc_history_search(out, "INC0011223") is False


def test_prompt_inc_history_search_accepts_yes(monkeypatch):
    from io import StringIO

    from rich.console import Console

    out = Console(file=StringIO(), width=120, highlight=False)
    monkeypatch.setattr("click.confirm", lambda *args, **kwargs: True)
    assert prompt_inc_history_search(out, "INC0011223") is True


def test_build_stats_rows_and_summary():
    now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    incidents = [
        {
            "id": "PONE",
            "incident_number": 101,
            "created_at": "2026-06-27T08:00:00Z",
            "resolved_at": "2026-06-27T10:00:00Z",
            "status": "resolved",
            "title": SAMPLE_TITLE,
            "service": {"summary": "Zephyr Labs"},
        },
        {
            "id": "PTWO",
            "incident_number": 102,
            "created_at": "2026-06-29T08:00:00Z",
            "resolved_at": None,
            "status": "triggered",
            "title": SAMPLE_TITLE,
            "service": {"summary": "Zephyr Labs"},
        },
    ]
    contexts = {
        "PONE": {
            "metadata": {"servicenow_itsm_example_INC0011223": "{}"},
            "linked_records": [],
        },
        "PTWO": {"metadata": {}, "linked_records": []},
    }
    rows = build_stats_rows(incidents, contexts=contexts, now=now)
    assert rows[0].ticket == "INC0011223"
    assert rows[1].ticket == "#102"
    assert rows[1].duration == "open (4h)"

    summary = summarize_stats(rows, reference_incident=incidents[1], now=now)
    assert summary.count == 2
    assert summary.resolved_count == 1
    assert summary.avg_duration == "2h"
    assert summary.current == "triggered (4h)"


def test_build_stats_rows_reads_notes_from_context():
    now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    incidents = [
        {
            "id": "PONE",
            "incident_number": 101,
            "created_at": "2026-06-27T08:00:00Z",
            "resolved_at": "2026-06-27T10:00:00Z",
            "status": "resolved",
            "title": SAMPLE_TITLE,
            "service": {"summary": "Zephyr Labs"},
        },
    ]
    contexts = {
        "PONE": {
            "metadata": {},
            "linked_records": [],
            "notes": [{"content": "<p>Checked replication</p>"}],
        },
    }
    rows = build_stats_rows(incidents, contexts=contexts, now=now)
    assert rows[0].notes == ["Checked replication"]
    assert rows[0].problem_candidates == []


def test_collect_notes_newest_first():
    notes = [
        {"content": "older note", "created_at": "2026-06-27T08:00:00Z"},
        {"content": "newest note", "created_at": "2026-06-29T10:00:00Z"},
        {"content": "middle note", "created_at": "2026-06-28T12:00:00Z"},
    ]
    assert collect_notes(notes) == ["newest note", "middle note", "older note"]


def test_collect_prb_candidates_finds_and_deduplicates():
    notes = [
        {"content": "Linked to PRB0123456 for root cause"},
        {"content": "<p>See also prb0123456 and PRB0999888</p>"},
    ]
    assert collect_prb_candidates(notes) == ["PRB0123456", "PRB0999888"]


def test_notes_from_log_entries_and_servicenow_clean():
    from pd_shift.stats import notes_from_log_entries

    log_entries = [
        {
            "type": "annotate_log_entry",
            "created_at": "2026-06-30T07:00:00Z",
            "channel": {
                "type": "note",
                "summary": "(from ServiceNow:jdoe) [code]<p>found that we have PRB0044556</p>",
            },
        },
        {
            "type": "resolve_log_entry",
            "summary": "Resolved by Jane Doe.",
            "channel": {"type": "api"},
        },
    ]
    raw_notes = notes_from_log_entries(log_entries)
    assert len(raw_notes) == 1
    cleaned = collect_notes(raw_notes)
    assert cleaned == ["found that we have PRB0044556"]
    assert collect_prb_candidates(raw_notes) == ["PRB0044556"]


def test_build_stats_rows_includes_problem_candidates():
    now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    incidents = [
        {
            "id": "PONE",
            "incident_number": 101,
            "created_at": "2026-06-27T08:00:00Z",
            "resolved_at": "2026-06-27T10:00:00Z",
            "status": "resolved",
            "title": SAMPLE_TITLE,
            "service": {"summary": "Zephyr Labs"},
        },
    ]
    contexts = {
        "PONE": {
            "metadata": {"servicenow_itsm_example_INC0011223": "{}"},
            "linked_records": [],
            "notes": [{"content": "Root cause tracked in PRB0044556"}],
        },
    }
    rows = build_stats_rows(incidents, contexts=contexts, now=now)
    assert rows[0].problem_candidates == ["PRB0044556"]


def test_render_stats_shows_problem_candidate(capsys):
    from io import StringIO

    from rich.console import Console

    from pd_shift.stats import StatsRow, StatsSummary

    out = Console(file=StringIO(), width=120, highlight=False)
    rows = [
        StatsRow(
            ticket="INC0011223",
            started="2026-06-27 08:00 UTC",
            resolved="2026-06-27 10:00 UTC",
            duration="2h",
            created_at=datetime(2026, 6, 27, 8, 0, tzinfo=timezone.utc),
            is_resolved=True,
            duration_delta=timedelta(hours=2),
            notes=[],
            problem_candidates=["PRB0044556"],
        ),
    ]
    summary = StatsSummary(
        count=1,
        per_week=0.1,
        avg_duration="2h",
        resolved_count=1,
        fire_range="08:00–11:00 UTC",
        fire_count=1,
        fire_start_hour=8,
        fire_window_hours=3,
        last_seen="2d ago",
        current="resolved",
        problem_candidates=["PRB0044556"],
    )
    render_stats(
        out,
        signature="MySQL Threads Running - db1",
        customer="Zephyr Labs",
        reference_label="INC0011223",
        rows=rows,
        summary=summary,
        show_notes=False,
    )
    text = out.file.getvalue()
    assert "Problem candidate:  PRB0044556" in text


def test_render_stats_highlights_atypical_fire():
    from io import StringIO

    from rich.console import Console

    from pd_shift.stats import StatsRow, StatsSummary

    out = Console(file=StringIO(), width=120, highlight=False)
    rows = [
        StatsRow(
            ticket="INC0000001",
            started="2026-06-14 08:32 UTC",
            resolved="2026-06-14 09:00 UTC",
            duration="28m",
            created_at=datetime(2026, 6, 14, 8, 32, tzinfo=timezone.utc),
            is_resolved=True,
            duration_delta=timedelta(minutes=28),
            notes=[],
            problem_candidates=[],
        ),
        StatsRow(
            ticket="INC0000002",
            started="2026-06-14 13:18 UTC",
            resolved="2026-06-14 13:30 UTC",
            duration="12m",
            created_at=datetime(2026, 6, 14, 13, 18, tzinfo=timezone.utc),
            is_resolved=True,
            duration_delta=timedelta(minutes=12),
            notes=[],
            problem_candidates=[],
        ),
    ]
    summary = StatsSummary(
        count=2,
        per_week=1.0,
        avg_duration="20m",
        resolved_count=2,
        fire_range="07:00–10:00 UTC",
        fire_count=1,
        fire_start_hour=7,
        fire_window_hours=3,
        last_seen="1d ago",
        current="resolved",
        problem_candidates=[],
    )
    render_stats(
        out,
        signature="MySQL Threads Running - db1",
        customer="Zephyr Labs",
        reference_label="INC0000001",
        rows=rows,
        summary=summary,
        show_notes=False,
    )
    text = out.file.getvalue()
    assert "INC0000002" in text
    assert " - atypical" in text
    assert "fired outside typical window" not in text


def test_format_stats_row_line_colors_atypical_times():
    from pd_shift.stats import ATYPICAL_FIRE_SUFFIX, _format_stats_row_line

    row = StatsRow(
        ticket="INC0000002",
        started="2026-06-14 13:18 UTC",
        resolved="2026-06-14 13:30 UTC",
        duration="12m",
        created_at=datetime(2026, 6, 14, 13, 18, tzinfo=timezone.utc),
        is_resolved=True,
        duration_delta=timedelta(minutes=12),
        notes=[],
        problem_candidates=[],
    )
    atypical = _format_stats_row_line(
        row,
        ticket_w=11,
        started_w=22,
        resolved_w=22,
        duration_w=8,
        atypical=True,
    )
    # Atypical rows keep the normal cyan date / green time; only the
    # " - atypical" tag is yellow.
    assert ATYPICAL_FIRE_SUFFIX in atypical.plain
    assert any(style and "cyan" in str(style) for _, _, style in atypical._spans)
    assert any(style and "green" in str(style) for _, _, style in atypical._spans)
    yellow_spans = [
        atypical.plain[start:end]
        for start, end, style in atypical._spans
        if style and "yellow" in str(style)
    ]
    assert yellow_spans == [ATYPICAL_FIRE_SUFFIX]

    typical = _format_stats_row_line(
        row,
        ticket_w=11,
        started_w=22,
        resolved_w=22,
        duration_w=8,
        atypical=False,
    )
    assert any(style and "cyan" in str(style) for _, _, style in typical._spans)
    assert any(style and "green" in str(style) for _, _, style in typical._spans)
    assert not any(
        style and "yellow" in str(style) for _, _, style in typical._spans
    )
    assert ATYPICAL_FIRE_SUFFIX not in typical.plain


def test_aggregate_problem_candidates_deduplicates():
    rows = [
        StatsRow("INC1", "", "", "", datetime.now(timezone.utc), True, None, [], ["PRB001"]),
        StatsRow("INC2", "", "", "", datetime.now(timezone.utc), True, None, [], ["PRB001", "PRB002"]),
    ]
    assert aggregate_problem_candidates(rows) == ["PRB001", "PRB002"]


def _times_at(*specs):
    """specs are (day, hour, minute) tuples in June 2026 UTC."""
    return [
        datetime(2026, 6, day, hour, minute, tzinfo=timezone.utc)
        for day, hour, minute in specs
    ]


def test_dominant_fire_minute_detects_daily_cluster():
    # 18 fires at exactly 08:32, 7 scattered elsewhere -> 18/25 dominant.
    times = [datetime(2026, 6, day, 8, 32, tzinfo=timezone.utc) for day in range(1, 19)]
    times += _times_at(
        (2, 13, 18), (3, 20, 42), (4, 3, 42), (5, 16, 5),
        (6, 22, 9), (7, 11, 50), (8, 1, 30),
    )
    assert dominant_fire_minute(times) == ("08:32", 18)


def test_dominant_fire_minute_groups_within_tolerance():
    # 08:33 and 08:36 are within +-5min of the modal 08:32 and group with it.
    times = _times_at((1, 8, 32), (2, 8, 32), (3, 8, 32), (4, 8, 33), (5, 8, 36))
    assert dominant_fire_minute(times) == ("08:32", 5)


def test_dominant_fire_minute_spread_returns_none():
    # One fire per hour: no +-5min window reaches the threshold.
    times = _times_at(*[(day, hour, 0) for day, hour in enumerate(range(0, 10), start=1)])
    assert dominant_fire_minute(times) is None


def test_dominant_fire_minute_below_min_count_returns_none():
    times = _times_at((1, 8, 32), (2, 8, 32))
    assert dominant_fire_minute(times) is None


def test_dominant_fire_minute_below_min_share_returns_none():
    # Cluster of 3 at 08:32 is exactly min_count, but 3/10 < 0.40 share.
    times = _times_at((1, 8, 32), (2, 8, 32), (3, 8, 32))
    times += _times_at(*[(day, hour, 0) for day, hour in enumerate(range(0, 7), start=4)])
    assert dominant_fire_minute(times) is None


def test_dominant_fire_minute_empty_returns_none():
    assert dominant_fire_minute([]) is None


def test_summarize_stats_sets_peak_for_clustered_fires():
    now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    rows = [
        StatsRow(
            ticket=f"INC{day:04d}",
            started="",
            resolved="",
            duration="1h",
            created_at=datetime(2026, 6, day, 8, 32, tzinfo=timezone.utc),
            is_resolved=True,
            duration_delta=timedelta(hours=1),
            notes=[],
            problem_candidates=[],
        )
        for day in range(1, 6)
    ]
    summary = summarize_stats(rows, reference_incident={"status": "resolved"}, now=now)
    assert summary.peak_time == "08:32"
    assert summary.peak_count == 5


def test_render_stats_shows_peak_annotation():
    from io import StringIO

    from rich.console import Console

    from pd_shift.stats import StatsRow, StatsSummary

    out = Console(file=StringIO(), width=120, highlight=False)
    rows = [
        StatsRow(
            ticket="INC0000001",
            started="2026-06-14 08:32 UTC",
            resolved="2026-06-14 09:00 UTC",
            duration="28m",
            created_at=datetime(2026, 6, 14, 8, 32, tzinfo=timezone.utc),
            is_resolved=True,
            duration_delta=timedelta(minutes=28),
            notes=[],
            problem_candidates=[],
        ),
    ]
    summary = StatsSummary(
        count=25,
        per_week=2.9,
        avg_duration="1h 22m",
        resolved_count=25,
        fire_range="07:00–10:00 UTC",
        fire_count=22,
        fire_start_hour=7,
        fire_window_hours=3,
        last_seen="51m ago",
        current="resolved",
        problem_candidates=[],
        peak_time="08:32",
        peak_count=18,
    )
    render_stats(
        out,
        signature="MySQL Threads Running - db1",
        customer="Zephyr Labs",
        reference_label="INC0011223",
        rows=rows,
        summary=summary,
        show_notes=False,
    )
    text = out.file.getvalue()
    assert "peak 08:32 (18/25)" in text


def test_render_stats_omits_peak_when_absent():
    from io import StringIO

    from rich.console import Console

    from pd_shift.stats import StatsRow, StatsSummary

    out = Console(file=StringIO(), width=120, highlight=False)
    rows = [
        StatsRow(
            ticket="INC0000001",
            started="2026-06-14 08:32 UTC",
            resolved="2026-06-14 09:00 UTC",
            duration="28m",
            created_at=datetime(2026, 6, 14, 8, 32, tzinfo=timezone.utc),
            is_resolved=True,
            duration_delta=timedelta(minutes=28),
            notes=[],
            problem_candidates=[],
        ),
    ]
    summary = StatsSummary(
        count=1,
        per_week=0.1,
        avg_duration="28m",
        resolved_count=1,
        fire_range="07:00–10:00 UTC",
        fire_count=1,
        fire_start_hour=7,
        fire_window_hours=3,
        last_seen="51m ago",
        current="resolved",
        problem_candidates=[],
    )
    render_stats(
        out,
        signature="MySQL Threads Running - db1",
        customer="Zephyr Labs",
        reference_label="INC0011223",
        rows=rows,
        summary=summary,
        show_notes=False,
    )
    text = out.file.getvalue()
    assert "peak" not in text


def test_summarize_stats_includes_problem_candidates():
    now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    rows = [
        StatsRow(
            ticket="INC0011223",
            started="",
            resolved="",
            duration="2h",
            created_at=datetime(2026, 6, 27, 8, 0, tzinfo=timezone.utc),
            is_resolved=True,
            duration_delta=timedelta(hours=2),
            notes=[],
            problem_candidates=["PRB0044556"],
        ),
    ]
    summary = summarize_stats(rows, reference_incident={"status": "resolved"}, now=now)
    assert summary.problem_candidates == ["PRB0044556"]
