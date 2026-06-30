from dataclasses import dataclass
from datetime import datetime, timezone

from rich.console import Console
from rich.text import Text

from pd_shift.parse import (
    customer_from_title,
    description_from_title,
    display_title_differs_from_pd,
    host_from_title,
    ticket_from_incident,
)
from pd_shift.timefmt import format_trigger_time, parse_pd_timestamp

from pd_shift.console_io import EMPTY

TRIGGERED = "triggered"
MISSING_TICKET = EMPTY
SEPARATOR = " - "


PD_SYNC_HINT = (
    "Description with underlined where it differs from PD — pd rename <INC> to sync"
)


def _print_sync_hint(console: Console) -> None:
    hint = Text()
    hint.append("Description with ", style="dim")
    hint.append("underlined", style="underline")
    hint.append(" where it differs from PD — pd rename <INC> to sync", style="dim")
    console.print()
    console.print(hint)


@dataclass
class IncidentRow:
    ticket: str
    customer: str
    description: str
    status: str
    triggered: str = EMPTY
    triggered_at: datetime | None = None
    description_differs_from_pd: bool = False

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
        description_differs_from_pd=display_title_differs_from_pd(title, service),
    )


def _customer_sort_key(name: str) -> str:
    return name.casefold() if name != EMPTY else "\uffff"


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


def _description_style(*, status: str, differs_from_pd: bool) -> str:
    base = _status_style(status)
    if differs_from_pd:
        return f"{base} underline"
    return base


def _render_row_text(
    row: IncidentRow,
    ticket_w: int,
    customer_w: int,
    *,
    show_time: bool,
    time_w: int,
) -> Text:
    prefix = (
        f"{row.ticket.ljust(ticket_w)}{SEPARATOR}"
        f"{row.customer.ljust(customer_w)}"
    )
    if show_time:
        prefix = f"{prefix}{SEPARATOR}{row.triggered.ljust(time_w)}"
    prefix = f"{prefix}{SEPARATOR}"

    line = Text()
    line.append(prefix, style=_status_style(row.status))
    line.append(
        row.description,
        style=_description_style(
            status=row.status,
            differs_from_pd=row.description_differs_from_pd,
        ),
    )
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

    show_sync_hint = False
    for row in rows:
        if row.description_differs_from_pd:
            show_sync_hint = True
        console.print(
            _render_row_text(
                row,
                ticket_w,
                customer_w,
                show_time=show_time,
                time_w=time_w,
            )
        )

    if show_sync_hint:
        _print_sync_hint(console)
