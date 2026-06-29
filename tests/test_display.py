from datetime import datetime, timezone

from pd_shift.display import IncidentRow, format_aligned_row, sort_incident_rows


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
        IncidentRow("—", "Alpha Co", "Alert no inc", "triggered"),
        IncidentRow("INC0011223", "Zephyr Labs", "Alert B", "acknowledged"),
        IncidentRow("INC0099887", "Alpha Co", "Alert A", "triggered"),
    ]
    sorted_rows = sort_incident_rows(rows)
    assert [row.ticket for row in sorted_rows] == [
        "INC0099887",
        "INC0011223",
        "—",
    ]
    assert sorted_rows[-1].customer == "Alpha Co"


def test_sort_missing_inc_last_within_customer():
    rows = [
        IncidentRow("—", "Zephyr Labs", "Alert no inc", "triggered"),
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
    assert sorted_rows[2].ticket == "—"


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
            "—",
            "Alpha Co",
            "Alert no inc",
            "acknowledged",
            triggered_at=datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc),
        ),
    ]
    sorted_rows = sort_incident_rows(rows, by_time=True)
    assert [row.ticket for row in sorted_rows] == ["INC0000003", "—", "INC0000001"]


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
