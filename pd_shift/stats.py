from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.text import Text

from pd_shift.htmltext import html_to_plain
from pd_shift.console_io import EMPTY
from pd_shift.parse import ticket_from_incident
from pd_shift.timefmt import (
    format_duration,
    format_timestamp_utc,
    format_timedelta_duration,
    format_trigger_time,
    parse_pd_timestamp,
)

SERVICENOW_URL_RE = re.compile(r"https://percona\.service-now\.com/\S*", re.IGNORECASE)
SEPARATOR = " - "
STATS_WINDOW_DAYS = 60
FIRE_WINDOW_HOURS = 3


def stats_alert_label(customer: str, signature: str) -> str:
    return f"{customer} - {signature}"


def print_inc_not_open_hint(console: Console, ticket: str) -> None:
    console.print(f"[yellow]{ticket} is not in open incidents.[/yellow]")
    console.print(
        "Use the PD incident number instead - in PagerDuty: "
        "[bold]Incidents -> Incident #123456[/bold]"
    )
    console.print("Then run:  [cyan]pd stats 123456[/cyan]")
    console.print(
        "Slow INC search (many API calls):  "
        f"[dim]pd stats {ticket} --yes[/dim]"
    )


@dataclass
class StatsRow:
    ticket: str
    started: str
    resolved: str
    duration: str
    created_at: datetime
    is_resolved: bool
    duration_delta: timedelta | None
    notes: list[str]


@dataclass
class StatsSummary:
    count: int
    per_week: float
    avg_duration: str
    resolved_count: int
    fire_range: str
    fire_count: int
    last_seen: str
    current: str


def clean_note(content: str) -> str | None:
    text = html_to_plain(content)
    text = SERVICENOW_URL_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip(" -|") for line in text.split("\n")]
    lines = [line for line in lines if line]
    text = "\n".join(lines)
    return text or None


def collect_notes(raw_notes: list[dict]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for note in raw_notes:
        content = clean_note(str(note.get("content") or ""))
        if not content or content in seen:
            continue
        seen.add(content)
        cleaned.append(content)
    return cleaned


def _format_fire_range(start_hour: int, window_hours: int) -> str:
    end_hour = (start_hour + window_hours) % 24
    return f"{start_hour:02d}:00–{end_hour:02d}:00 UTC"


def typical_fire(created_times: list[datetime], *, window_hours: int = FIRE_WINDOW_HOURS) -> tuple[str, int]:
    if not created_times:
        return EMPTY, 0

    counts = [0] * 24
    for created in created_times:
        counts[created.astimezone(timezone.utc).hour] += 1

    best_start = 0
    best_count = 0
    for start in range(24):
        count = sum(counts[(start + offset) % 24] for offset in range(window_hours))
        if count > best_count:
            best_count = count
            best_start = start
        elif count == best_count and counts[start] > counts[best_start]:
            best_start = start

    return _format_fire_range(best_start, window_hours), best_count


def _duration_delta(created_at: str | None, resolved_at: str | None, *, now: datetime) -> timedelta | None:
    if not created_at:
        return None
    started = parse_pd_timestamp(created_at)
    if resolved_at:
        ended = parse_pd_timestamp(resolved_at)
        delta = ended - started
        return delta if delta.total_seconds() >= 0 else None
    delta = now.astimezone(timezone.utc) - started.astimezone(timezone.utc)
    return delta if delta.total_seconds() >= 0 else None


def build_stats_rows(
    incidents: list[dict],
    *,
    contexts: dict[str, dict],
    notes_by_id: dict[str, list[dict]] | None = None,
    now: datetime | None = None,
) -> list[StatsRow]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    rows: list[StatsRow] = []
    for incident in incidents:
        incident_id = incident["id"]
        context = contexts.get(incident_id, {})
        ticket = ticket_from_incident(
            metadata=context.get("metadata"),
            linked_records=context.get("linked_records", []),
        )
        number = incident.get("incident_number")
        ticket_label = ticket or (f"#{number}" if number else incident_id)
        created_at = incident.get("created_at")
        resolved_at = incident.get("resolved_at")
        is_resolved = bool(resolved_at)
        duration_delta = _duration_delta(created_at, resolved_at, now=current)
        rows.append(
            StatsRow(
                ticket=ticket_label,
                started=format_timestamp_utc(created_at),
                resolved=format_timestamp_utc(resolved_at) if resolved_at else EMPTY,
                duration=format_duration(created_at, resolved_at, now=current),
                created_at=parse_pd_timestamp(created_at) if created_at else datetime.min.replace(tzinfo=timezone.utc),
                is_resolved=is_resolved,
                duration_delta=duration_delta,
                notes=collect_notes(notes_by_id.get(incident_id, [])) if notes_by_id else [],
            )
        )

    rows.sort(key=lambda row: row.created_at, reverse=True)
    return rows


def summarize_stats(
    rows: list[StatsRow],
    *,
    reference_incident: dict,
    window_days: int = STATS_WINDOW_DAYS,
    now: datetime | None = None,
) -> StatsSummary:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    count = len(rows)
    per_week = count / (window_days / 7) if count else 0.0

    resolved_deltas = [row.duration_delta for row in rows if row.is_resolved and row.duration_delta]
    resolved_count = len(resolved_deltas)
    if resolved_deltas:
        avg_seconds = sum(delta.total_seconds() for delta in resolved_deltas) / len(resolved_deltas)
        avg_duration = format_timedelta_duration(timedelta(seconds=avg_seconds))
    else:
        avg_duration = EMPTY

    created_times = [row.created_at for row in rows]
    fire_range, fire_count = typical_fire(created_times)
    last_seen = format_trigger_time(
        rows[0].created_at.isoformat() if rows else None,
        now=current,
    )

    ref_status = reference_incident.get("status", "unknown")
    ref_created = reference_incident.get("created_at")
    if ref_status in ("triggered", "acknowledged") and ref_created:
        open_for = format_duration(ref_created, None, now=current).removeprefix("open (").removesuffix(")")
        current_line = f"{ref_status} ({open_for})"
    else:
        current_line = ref_status

    return StatsSummary(
        count=count,
        per_week=per_week,
        avg_duration=avg_duration,
        resolved_count=resolved_count,
        fire_range=fire_range,
        fire_count=fire_count,
        last_seen=last_seen,
        current=current_line,
    )


def _column_widths(rows: list[StatsRow], *, show_notes: bool) -> tuple[int, int, int, int]:
    ticket_w = max(len("INC"), *(len(row.ticket) for row in rows))
    started_w = max(len("STARTED"), *(len(row.started) for row in rows))
    resolved_w = max(len("RESOLVED"), *(len(row.resolved) for row in rows))
    duration_w = max(len("DURATION"), *(len(row.duration) for row in rows))
    return ticket_w, started_w, resolved_w, duration_w


def _print_note_line(console: Console, line: str, *, first: bool) -> None:
    if first:
        text = Text("  ")
        text.append("note:", style="dim")
        text.append(f" {line}")
    else:
        text = Text(f"         {line}")
    console.print(text)


def render_stats(
    console: Console,
    *,
    signature: str,
    customer: str,
    reference_label: str,
    rows: list[StatsRow],
    summary: StatsSummary,
    show_notes: bool,
    window_days: int = STATS_WINDOW_DAYS,
) -> None:
    console.print(f"{reference_label}  -  {signature}  ({customer})")
    console.print()

    if not rows:
        console.print(f"[dim]No matching incidents in the last {window_days} days.[/dim]")
        return

    ticket_w, started_w, resolved_w, duration_w = _column_widths(rows, show_notes=show_notes)
    gap = " " * len(SEPARATOR)
    header = (
        f"{'INC'.ljust(ticket_w)}{gap}"
        f"{'STARTED'.ljust(started_w)}{gap}"
        f"{'RESOLVED'.ljust(resolved_w)}{gap}"
        f"{'DURATION'.ljust(duration_w)}"
    )
    console.print(header, style="dim")

    for row in rows:
        line = (
            f"{row.ticket.ljust(ticket_w)}{SEPARATOR}"
            f"{row.started.ljust(started_w)}{SEPARATOR}"
            f"{row.resolved.ljust(resolved_w)}{SEPARATOR}"
            f"{row.duration.ljust(duration_w)}"
        )
        console.print(line)
        if show_notes and row.notes:
            for note in row.notes:
                for index, note_line in enumerate(note.split("\n")):
                    _print_note_line(console, note_line, first=index == 0)

    console.print()
    console.print(f"Last {window_days} days:  {summary.count} incidents  (~{summary.per_week:.1f}/week)")
    if summary.resolved_count:
        console.print(
            f"Avg duration:   {summary.avg_duration}  ({summary.resolved_count} resolved)"
        )
    else:
        console.print(f"Avg duration:   {EMPTY}  (0 resolved)")
    if summary.count:
        console.print(
            f"Typical fire:   {summary.fire_range}  ({summary.fire_count}/{summary.count})"
        )
    else:
        console.print(f"Typical fire:   {EMPTY}")
    console.print(f"Last seen:      {summary.last_seen}")
    console.print(f"Current:        {summary.current}")
