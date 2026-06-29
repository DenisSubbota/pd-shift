from datetime import datetime, timedelta, timezone

from pd_shift.parse import alert_signature, incident_matches_signature
from pd_shift.stats import (
    build_stats_rows,
    clean_note,
    collect_notes,
    print_inc_not_open_hint,
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


def test_incident_does_not_match_other_customer():
    incident = {
        "title": "[Alpha Co] Percona_MS_X - CRITICAL - MySQL Threads Running - db1",
        "service": {"summary": "Alpha Co"},
    }
    customer, signature = alert_signature(SAMPLE_TITLE, "Zephyr Labs")
    assert not incident_matches_signature(incident, customer, signature)


def test_clean_note_strips_servicenow_url():
    note = (
        "Checked threads. See https://percona.service-now.com/incident.do?sys_id=abc "
        "for details."
    )
    assert clean_note(note) == "Checked threads. See for details."


def test_collect_notes_deduplicates():
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    times = [base.replace(hour=3), base.replace(hour=4), base.replace(hour=22)]
    fire_range, count = typical_fire(times, window_hours=3)
    assert fire_range == "03:00–06:00 UTC"
    assert count == 2


def test_print_inc_not_open_hint(capsys):
    from io import StringIO

    from rich.console import Console

    out = Console(file=StringIO(), width=120, force_terminal=True)
    print_inc_not_open_hint(out, "INC0011223")
    text = out.file.getvalue()
    assert "INC0011223 is not in open incidents" in text
    assert "pd stats 123456" in text
    assert "pd stats INC0011223 --yes" in text


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
    assert rows[0].ticket == "#102"
    assert rows[1].ticket == "INC0011223"
    assert rows[0].duration == "open (4h)"

    summary = summarize_stats(rows, reference_incident=incidents[1], now=now)
    assert summary.count == 2
    assert summary.resolved_count == 1
    assert summary.avg_duration == "2h"
    assert summary.current == "triggered (4h)"
