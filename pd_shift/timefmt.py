from __future__ import annotations

from datetime import datetime, timedelta, timezone


def parse_pd_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone()


def format_trigger_time(
    created_at: str | None,
    *,
    now: datetime | None = None,
) -> str:
    if not created_at:
        return "—"

    triggered = parse_pd_timestamp(created_at)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone()

    delta = current - triggered.astimezone(current.tzinfo)
    if delta < timedelta(0):
        return triggered.strftime("%Y-%m-%d")

    if delta >= timedelta(days=7):
        return triggered.astimezone(current.tzinfo).strftime("%Y-%m-%d")

    days = delta.days
    if days >= 1:
        return f"{days}d ago"

    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours}h ago"

    minutes = delta.seconds // 60
    if minutes >= 1:
        return f"{minutes}m ago"

    return "just now"


def format_timestamp_utc(value: str | None) -> str:
    if not value:
        return "—"
    return parse_pd_timestamp(value).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def format_duration(
    start: str | None,
    end: str | None,
    *,
    now: datetime | None = None,
) -> str:
    if not start:
        return "—"

    started = parse_pd_timestamp(start)
    if end:
        ended = parse_pd_timestamp(end)
        delta = ended - started
        if delta.total_seconds() < 0:
            return "—"
        return _format_timedelta(delta)

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    delta = current.astimezone(timezone.utc) - started.astimezone(timezone.utc)
    if delta.total_seconds() < 0:
        return "—"
    open_for = _format_timedelta(delta)
    return f"open ({open_for})"


def format_timedelta_duration(delta: timedelta) -> str:
    if delta.total_seconds() < 0:
        return "—"
    return _format_timedelta(delta)


def _format_timedelta(delta: timedelta) -> str:
    total_minutes = int(delta.total_seconds()) // 60
    if total_minutes < 1:
        return "just now"

    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "just now"
