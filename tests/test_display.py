from datetime import datetime, timezone

from io import StringIO

from rich.console import Console
from rich.text import Text

from pd_shift.display import (
    IncidentRow,
    _description_style,
    _render_row_text,
    format_aligned_row,
    render_incident_table,
    sort_incident_rows,
)


def test_sort_groups_by_customer():
    rows = [
        IncidentRow("INC0000003", "Zephyr Labs", "Alert C", "triggered"),
        IncidentRow("INC0000001", "Alpha Co", "Alert A", "triggered"),
        IncidentRow("INC0000002", "Zephyr Labs", "Alert B", "acknowledged"),
    ]
    sorted_rows = sort_incident_rows(rows)
    assert [row.customer for row in sorted_rows] == [
        "Alpha Co",
        "Zephyr Labs",
        "Zephyr Labs",
    ]
    assert [row.ticket for row in sorted_rows] == [
        "INC0000001",
        "INC0000003",
        "INC0000002",
    ]


def test_sort_no_inc_at_bottom_globally():
    rows = [
        IncidentRow("-", "Alpha Co", "Alert no inc", "triggered"),
        IncidentRow("INC0011223", "Zephyr Labs", "Alert B", "acknowledged"),
        IncidentRow("INC0099887", "Alpha Co", "Alert A", "triggered"),
    ]
    sorted_rows = sort_incident_rows(rows)
    assert [row.ticket for row in sorted_rows] == [
        "INC0099887",
        "INC0011223",
        "-",
    ]
    assert sorted_rows[-1].customer == "Alpha Co"


def test_sort_missing_inc_last_within_customer():
    rows = [
        IncidentRow("-", "Zephyr Labs", "Alert no inc", "triggered"),
        IncidentRow("INC0011223", "Zephyr Labs", "Alert B", "acknowledged"),
        IncidentRow("INC0099887", "Alpha Co", "Alert A", "triggered"),
    ]
    sorted_rows = sort_incident_rows(rows)
    assert [row.customer for row in sorted_rows] == [
        "Alpha Co",
        "Zephyr Labs",
        "Zephyr Labs",
    ]
    assert sorted_rows[1].ticket == "INC0011223"
    assert sorted_rows[2].ticket == "-"


def test_sort_triggered_before_acked_within_customer():
    rows = [
        IncidentRow("INC0000002", "Zephyr Labs", "Alert B", "acknowledged"),
        IncidentRow("INC0000001", "Zephyr Labs", "Alert A", "triggered"),
    ]
    sorted_rows = sort_incident_rows(rows)
    assert [row.status for row in sorted_rows] == ["triggered", "acknowledged"]


def test_sort_by_time_ignores_customer_and_inc():
    rows = [
        IncidentRow(
            "INC0000003",
            "Zephyr Labs",
            "Alert C",
            "triggered",
            triggered_at=datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc),
        ),
        IncidentRow(
            "INC0000001",
            "Alpha Co",
            "Alert A",
            "triggered",
            triggered_at=datetime(2026, 6, 27, 8, 0, tzinfo=timezone.utc),
        ),
        IncidentRow(
            "-",
            "Alpha Co",
            "Alert no inc",
            "acknowledged",
            triggered_at=datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc),
        ),
    ]
    sorted_rows = sort_incident_rows(rows, by_time=True)
    assert [row.ticket for row in sorted_rows] == ["INC0000003", "-", "INC0000001"]


def test_format_aligned_row_uses_dash_separators():
    rows = [
        IncidentRow(
            "INC0011223",
            "Zephyr Labs",
            "Disk Space Low - zephyr-db-01",
            "triggered",
            triggered="2d ago",
        ),
        IncidentRow(
            "INC0044556",
            "Northwind LLC",
            "Replication Lag - db-replica-2",
            "acknowledged",
            triggered="4h ago",
        ),
    ]
    line = format_aligned_row(rows[0], ticket_w=11, customer_w=13, show_time=True, time_w=8)
    assert line == "INC0011223  - Zephyr Labs   - 2d ago   - Disk Space Low - zephyr-db-01"


def test_render_incident_table_shows_sync_hint_when_description_differs():
    rows = [
        IncidentRow(
            "INC0011223",
            "Zephyr Labs",
            "Disk Space Low - zephyr-db-01",
            "triggered",
            description_differs_from_pd=True,
        ),
        IncidentRow(
            "INC0044556",
            "Northwind LLC",
            "Replication Lag - db-replica-2",
            "acknowledged",
        ),
    ]
    output = StringIO()
    render_incident_table(Console(file=output, force_terminal=True, width=120), rows)
    text = output.getvalue()
    assert "Disk Space Low - zephyr-db-01" in text
    # The word "underlined" is itself underlined in the legend, so ANSI codes
    # split the phrase under force_terminal; assert the styling-stable pieces.
    assert "Description with" in text
    assert "where it differs from PD" in text
    assert "pd rename <INC>" in text


def test_render_row_underlines_description_when_differs():
    row = IncidentRow(
        "INC0011223",
        "Zephyr Labs",
        "Disk Space Low - zephyr-db-01",
        "triggered",
        description_differs_from_pd=True,
    )
    rendered = _render_row_text(row, ticket_w=11, customer_w=13, show_time=False, time_w=0)
    desc = "Disk Space Low - zephyr-db-01"
    start = rendered.plain.index(desc)
    end = start + len(desc)
    styles = [style for span_start, span_end, style in rendered._spans if span_start <= start and span_end >= end]
    assert styles
    assert any("underline" in str(style) for style in styles)


def test_description_style_adds_underline_when_differs():
    assert "underline" in _description_style(status="triggered", differs_from_pd=True)
    assert "underline" not in _description_style(status="triggered", differs_from_pd=False)


def test_sync_hint_underlines_the_word_underlined():
    from pd_shift.display import _print_sync_hint

    output = StringIO()
    _print_sync_hint(Console(file=output, force_terminal=True, width=120))
    rendered = output.getvalue()
    assert "underlined" in rendered.replace("\n", "")

    hint = Text()
    hint.append("underlined", style="underline")
    styles = [style for _start, _end, style in hint._spans if "underline" in str(style)]
    assert styles
    assert "dim" not in str(styles[0])


def test_render_incident_table_no_sync_hint_when_all_aligned():
    rows = [
        IncidentRow("INC0011223", "Zephyr Labs", "Disk Space Low - zephyr-db-01", "triggered"),
    ]
    output = StringIO()
    render_incident_table(Console(file=output, force_terminal=True, width=120), rows)
    assert "pd rename" not in output.getvalue()
