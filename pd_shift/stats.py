from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import click
from rich.console import Console
from rich.text import Text

from pd_shift.htmltext import html_to_plain
from pd_shift.console_io import EMPTY, GREEN_STYLE
from pd_shift.parse import ticket_from_incident
from pd_shift.timefmt import (
    format_duration,
    format_timestamp_utc,
    format_timedelta_duration,
    format_trigger_time,
    parse_pd_timestamp,
)

SERVICENOW_URL_RE = re.compile(r"https://percona\.service-now\.com/\S*", re.IGNORECASE)
SERVICENOW_NOTE_PREFIX_RE = re.compile(
    r"^\(from ServiceNow:[^)]+\)\s*(\[code\])?\s*",
    re.IGNORECASE,
)
PRB_RE = re.compile(r"PRB\d+", re.IGNORECASE)
SEPARATOR = " - "
STATS_WINDOW_DAYS = 60
FIRE_WINDOW_HOURS = 3
# A tight single-minute cluster is the fingerprint of a scheduled trigger
# (cron, backup window, batch job) rather than organic load.
PEAK_TOLERANCE_MIN = 5
PEAK_MIN_SHARE = 0.40
PEAK_MIN_COUNT = 3
STATS_DATE_STYLE = "bold cyan"
STATS_TIME_STYLE = GREEN_STYLE
STATS_HEADER_STYLE = "dim"
ATYPICAL_TAG_STYLE = "yellow"
ATYPICAL_FIRE_SUFFIX = " - atypical"


def stats_alert_label(customer: str, signature: str) -> str:
    return f"{customer} - {signature}"


def print_inc_not_open_hint(console: Console, ticket: str) -> None:
    console.print(f"[yellow]{ticket} is not in open incidents.[/yellow]")
    console.print(
        "Use the PD incident number instead - in PagerDuty: "
        "[bold]Incidents -> Incident #123456[/bold]"
    )
    console.print("Then run:  [cyan]pd stats 123456[/cyan]")


def prompt_inc_history_search(console: Console, ticket: str) -> bool:
    """Ask whether to run the slow team-history INC lookup."""
    print_inc_not_open_hint(console, ticket)
    console.print()
    return click.confirm(
        "Search team history by INC? (slow, many API calls)",
        default=False,
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
    problem_candidates: list[str]


@dataclass
class StatsSummary:
    count: int
    per_week: float
    avg_duration: str
    resolved_count: int
    fire_range: str
    fire_count: int
    fire_start_hour: int
    fire_window_hours: int
    last_seen: str
    current: str
    problem_candidates: list[str]
    peak_time: str | None = None
    peak_count: int = 0


def clean_note(content: str) -> str | None:
    text = html_to_plain(content)
    text = SERVICENOW_NOTE_PREFIX_RE.sub("", text)
    text = SERVICENOW_URL_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip(" -|") for line in text.split("\n")]
    lines = [line for line in lines if line]
    text = "\n".join(lines)
    return text or None


def collect_notes(raw_notes: list[dict]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for note in _sort_notes_newest_first(raw_notes):
        content = clean_note(str(note.get("content") or ""))
        if not content or content in seen:
            continue
        seen.add(content)
        cleaned.append(content)
    return cleaned


def notes_from_log_entries(log_entries: list[dict]) -> list[dict]:
    """ServiceNow notes often appear only as annotate_log_entry channel notes."""
    notes: list[dict] = []
    for entry in log_entries:
        if entry.get("type") != "annotate_log_entry":
            continue
        channel = entry.get("channel") or {}
        if channel.get("type") != "note":
            continue
        content = str(channel.get("summary") or "").strip()
        if not content:
            continue
        notes.append(
            {
                "content": content,
                "created_at": entry.get("created_at"),
            }
        )
    return notes


def collect_prb_candidates(raw_notes: list[dict]) -> list[str]:
    seen: set[str] = set()
    candidates: list[str] = []
    for note in _sort_notes_newest_first(raw_notes):
        content = str(note.get("content") or "")
        for match in PRB_RE.findall(content):
            prb = match.upper()
            if prb in seen:
                continue
            seen.add(prb)
            candidates.append(prb)
    return candidates


def _sort_notes_newest_first(raw_notes: list[dict]) -> list[dict]:
    def sort_key(note: dict) -> datetime:
        created_at = note.get("created_at")
        if not created_at:
            return datetime.min.replace(tzinfo=timezone.utc)
        return parse_pd_timestamp(str(created_at)).astimezone(timezone.utc)

    return sorted(raw_notes, key=sort_key, reverse=True)


def aggregate_problem_candidates(rows: list[StatsRow]) -> list[str]:
    seen: set[str] = set()
    aggregated: list[str] = []
    for row in rows:
        for prb in row.problem_candidates:
            if prb in seen:
                continue
            seen.add(prb)
            aggregated.append(prb)
    return aggregated


def _format_fire_range(start_hour: int, window_hours: int) -> str:
    end_hour = (start_hour + window_hours) % 24
    return f"{start_hour:02d}:00–{end_hour:02d}:00 UTC"


def typical_fire(
    created_times: list[datetime],
    *,
    window_hours: int = FIRE_WINDOW_HOURS,
) -> tuple[str, int, int]:
    if not created_times:
        return EMPTY, 0, 0

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

    return _format_fire_range(best_start, window_hours), best_count, best_start


def dominant_fire_minute(
    created_times: list[datetime],
    *,
    tolerance_min: int = PEAK_TOLERANCE_MIN,
    min_share: float = PEAK_MIN_SHARE,
    min_count: int = PEAK_MIN_COUNT,
) -> tuple[str, int] | None:
    """Detect a precise recurring clock minute (a scheduled-trigger signal).

    Returns ("HH:MM", count) for the tightest ``+-tolerance_min`` cluster when
    it holds at least ``min_count`` incidents and ``min_share`` of the total;
    otherwise None. No midnight wraparound: a cluster straddling 23:59->00:01
    is not merged.
    """
    total = len(created_times)
    if not total:
        return None

    minutes = [t.astimezone(timezone.utc).hour * 60 + t.minute for t in created_times]
    exact_counts: dict[int, int] = {}
    for minute in minutes:
        exact_counts[minute] = exact_counts.get(minute, 0) + 1

    best_center = None
    best_window = 0
    # Tie-break: prefer the more populated exact minute, then the earlier one.
    for center in sorted(exact_counts):
        window = sum(
            count
            for minute, count in exact_counts.items()
            if abs(minute - center) <= tolerance_min
        )
        if window > best_window or (
            window == best_window
            and best_center is not None
            and exact_counts[center] > exact_counts[best_center]
        ):
            best_window = window
            best_center = center

    if best_center is None or best_window < min_count or best_window / total < min_share:
        return None

    return f"{best_center // 60:02d}:{best_center % 60:02d}", best_window


def is_typical_fire_time(
    created_at: datetime,
    *,
    start_hour: int,
    window_hours: int = FIRE_WINDOW_HOURS,
) -> bool:
    hour = created_at.astimezone(timezone.utc).hour
    return any(hour == (start_hour + offset) % 24 for offset in range(window_hours))


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
        raw_notes = context.get("notes", []) if "notes" in context else []
        rows.append(
            StatsRow(
                ticket=ticket_label,
                started=format_timestamp_utc(created_at),
                resolved=format_timestamp_utc(resolved_at) if resolved_at else EMPTY,
                duration=format_duration(created_at, resolved_at, now=current),
                created_at=parse_pd_timestamp(created_at) if created_at else datetime.min.replace(tzinfo=timezone.utc),
                is_resolved=is_resolved,
                duration_delta=duration_delta,
                notes=collect_notes(raw_notes) if "notes" in context else [],
                problem_candidates=collect_prb_candidates(raw_notes) if "notes" in context else [],
            )
        )

    rows.sort(key=lambda row: row.created_at)
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
    fire_range, fire_count, fire_start_hour = typical_fire(created_times)
    peak = dominant_fire_minute(created_times)
    newest = max(row.created_at for row in rows) if rows else None
    last_seen = format_trigger_time(
        newest.isoformat() if newest else None,
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
        fire_start_hour=fire_start_hour,
        fire_window_hours=FIRE_WINDOW_HOURS,
        last_seen=last_seen,
        current=current_line,
        problem_candidates=aggregate_problem_candidates(rows),
        peak_time=peak[0] if peak else None,
        peak_count=peak[1] if peak else 0,
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


def _append_stats_timestamp(line: Text, value: str, width: int) -> None:
    if value == EMPTY or " UTC" not in value:
        line.append(value.ljust(width))
        return
    date_part, time_part = value.split(" ", 1)
    line.append(date_part, style=STATS_DATE_STYLE)
    line.append(" ")
    line.append(time_part, style=STATS_TIME_STYLE)
    if len(value) < width:
        line.append(" " * (width - len(value)))


def _format_stats_row_line(
    row: StatsRow,
    *,
    ticket_w: int,
    started_w: int,
    resolved_w: int,
    duration_w: int,
    atypical: bool,
) -> Text:
    line = Text()
    line.append(f"{row.ticket.ljust(ticket_w)}{SEPARATOR}")
    _append_stats_timestamp(line, row.started, started_w)
    line.append(SEPARATOR)
    _append_stats_timestamp(line, row.resolved, resolved_w)
    line.append(SEPARATOR)
    line.append(row.duration.ljust(duration_w))
    if atypical:
        line.append(ATYPICAL_FIRE_SUFFIX, style=ATYPICAL_TAG_STYLE)
    return line


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
    console.print(header, style=STATS_HEADER_STYLE)

    for row in rows:
        atypical = not is_typical_fire_time(
            row.created_at,
            start_hour=summary.fire_start_hour,
            window_hours=summary.fire_window_hours,
        )
        line = _format_stats_row_line(
            row,
            ticket_w=ticket_w,
            started_w=started_w,
            resolved_w=resolved_w,
            duration_w=duration_w,
            atypical=atypical,
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
        typical_fire = Text()
        typical_fire.append("Typical fire:   ")
        typical_fire.append(summary.fire_range, style=STATS_TIME_STYLE)
        typical_fire.append(f"  ({summary.fire_count}/{summary.count})")
        if summary.peak_time:
            typical_fire.append(" — peak ")
            typical_fire.append(summary.peak_time, style=STATS_TIME_STYLE)
            typical_fire.append(f" ({summary.peak_count}/{summary.count})")
        console.print(typical_fire)
    else:
        console.print(f"Typical fire:   {EMPTY}")
    console.print(f"Last seen:      {summary.last_seen}")
    console.print(f"Current:        {summary.current}")
    if summary.problem_candidates:
        console.print(f"Problem candidate:  {', '.join(summary.problem_candidates)}")
