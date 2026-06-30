# Stats: precise "peak" fire-time annotation + sub-minute duration fix

Date: 2026-06-30

## Motivation

`pd stats` already reports a fuzzy 3-hour "Typical fire" window. But recurring
alerts for scheduled workloads (cron jobs, backup windows, batch processes) fire
at a *precise* clock minute — e.g. 08:32 UTC nearly every day. That razor-sharp
signal is the fingerprint of a machine-scheduled trigger and is actionable DBA
intel, but the 3-hour window smears it away.

Separately, the duration column shows `just now` for resolved incidents that
lasted under a minute. `just now` is a *relative-time* phrase ("X ago") leaking
into a *duration* column; it should read like a duration.

## Part 1 — Peak fire-time annotation

Annotate the existing line (no new line, no replacement) when a tight cluster
dominates:

```
Typical fire:   07:00–10:00 UTC — peak 08:32 (18/25)
```

When fires are spread out, the line is unchanged (no annotation).

### New pure helper in `pd_shift/stats.py`

```python
def dominant_fire_minute(
    created_times: list[datetime],
    *,
    tolerance_min: int = PEAK_TOLERANCE_MIN,   # 5
    min_share: float = PEAK_MIN_SHARE,         # 0.40
    min_count: int = PEAK_MIN_COUNT,           # 3
) -> tuple[str, int] | None:
```

Algorithm:
1. Map each `created_at` to minute-of-day in UTC (`hour*60 + minute`).
2. For each distinct minute present, count incidents within `±tolerance_min`.
3. Take the minute with the highest window-count as the cluster center.
4. Return `("HH:MM", count)` only if `count >= min_count` **and**
   `count / total >= min_share`; otherwise `None`.

Thresholds are module constants alongside `FIRE_WINDOW_HOURS`.

### Wiring

- `StatsSummary` gains `peak_time: str | None` and `peak_count: int`.
- `summarize_stats` calls `dominant_fire_minute(created_times)` (it already
  builds `created_times`) and fills the fields.
- `render_stats` appends ` — peak {peak_time} ({peak_count}/{count})` to the
  Typical fire line only when `peak_time` is set, with the time in
  `STATS_TIME_STYLE` green to match the window.

### Scope guards (YAGNI)

- Single peak only — no second cluster.
- No midnight-wraparound grouping: a cluster straddling 23:59→00:01 won't merge.
  Documented limitation; near-zero in practice for daily jobs.

## Part 2 — Sub-minute duration fix

In `pd_shift/timefmt.py`, `_format_timedelta` returns `"just now"` when
`total_minutes < 1`. Change the duration formatter to return `"<1m"` instead.

- Affects `format_duration` (resolved + open: `open (<1m)`) and
  `format_timedelta_duration` (avg duration).
- `format_trigger_time` is untouched — its `"just now"` is a correct relative
  time for "Last seen".

## Tests (`tests/test_stats.py`, `tests/test_timefmt.py`)

`dominant_fire_minute`:
- dominant cluster returns center minute + count;
- ±5min jitter (08:32 / 08:33 / 08:36) groups into one cluster;
- spread-out times return `None`;
- below `min_count` returns `None`;
- below `min_share` returns `None`.

`render_stats`: annotation present when a peak exists; absent otherwise.

`_format_timedelta` / `format_duration`:
- sub-minute resolved duration renders `<1m` (not `just now`);
- sub-minute open incident renders `open (<1m)`;
- `format_trigger_time` still returns `just now` for a just-created incident.
```

