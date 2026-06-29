from dataclasses import dataclass
from datetime import datetime, timezone

from rich.console import Console
from rich.text import Text

from pd_shift.parse import (
    customer_from_title,
    description_from_title,
    host_from_title,
    ticket_from_incident,
)
from pd_shift.timefmt import format_trigger_time, parse_pd_timestamp

TRIGGERED = "triggered"
MISSING_TICKET = "—"
SEPARATOR = " - "


@dataclass
class IncidentRow:
    ticket: str
    customer: str
    description: str
    status: str
    triggered: str = "—"
    triggered_at: datetime | None = None

    @property
    def has_ticket(self) -> bool:
        return self.ticket != MISSING_TICKET


def _status_style(status: str) -> str:
    if status == TRIGGERED:
        return "bold red"
    return "white"


def incident_row_from(
    *,
    status: str,
    title: str,
    service: str,
    metadata: dict | None,
    linked_records: list[dict],
    created_at: str | None = None,
) -> IncidentRow:
    customer = customer_from_title(title, service)
    host = host_from_title(title)
    description = description_from_title(title, customer, host)
    ticket = ticket_from_incident(metadata=metadata, linked_records=linked_records)
    triggered_at = parse_pd_timestamp(created_at) if created_at else None
    return IncidentRow(
        ticket=ticket or MISSING_TICKET,
        customer=customer,
        description=description,
        status=status,
        triggered=format_trigger_time(created_at),
        triggered_at=triggered_at,
    )


def _customer_sort_key(name: str) -> str:
    return name.casefold() if name != "—" else "\uffff"


def sort_incident_rows(rows: list[IncidentRow], *, by_time: bool = False) -> list[IncidentRow]:
    if by_time:
        return sorted(
            rows,
            key=lambda row: row.triggered_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

    status_rank = {TRIGGERED: 0, "acknowledged": 1}

    return sorted(
        rows,
        key=lambda row: (
            not row.has_ticket,
            _customer_sort_key(row.customer),
            status_rank.get(row.status, 2),
            row.ticket.casefold() if row.has_ticket else row.description.casefold(),
        ),
    )


def _column_widths(rows: list[IncidentRow], *, show_time: bool) -> tuple[int, int, int]:
    ticket_w = max(len("TICKET"), *(len(row.ticket) for row in rows))
    customer_w = max(len("CUSTOMER"), *(len(row.customer) for row in rows))
    time_w = max(len("TRIGGERED"), *(len(row.triggered) for row in rows)) if show_time else 0
    return ticket_w, customer_w, time_w


def format_aligned_row(
    row: IncidentRow,
    ticket_w: int,
    customer_w: int,
    *,
    show_time: bool = False,
    time_w: int = 0,
) -> str:
    line = (
        f"{row.ticket.ljust(ticket_w)}{SEPARATOR}"
        f"{row.customer.ljust(customer_w)}"
    )
    if show_time:
        line = f"{line}{SEPARATOR}{row.triggered.ljust(time_w)}"
    line = f"{line}{SEPARATOR}{row.description}"
    return line


def render_incident_table(console: Console, rows: list[IncidentRow], *, show_time: bool = False) -> None:
    if not rows:
        return

    ticket_w, customer_w, time_w = _column_widths(rows, show_time=show_time)
    gap = " " * len(SEPARATOR)
    header = (
        f"{'TICKET'.ljust(ticket_w)}{gap}"
        f"{'CUSTOMER'.ljust(customer_w)}"
    )
    if show_time:
        header = f"{header}{gap}{'TRIGGERED'.ljust(time_w)}"
    header = f"{header}{gap}DESCRIPTION"
    console.print(header, style="dim")

    for row in rows:
        line = format_aligned_row(
            row,
            ticket_w,
            customer_w,
            show_time=show_time,
            time_w=time_w,
        )
        console.print(Text(line, style=_status_style(row.status)))
