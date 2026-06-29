# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`pd-shift` is a Click-based CLI (`pd`) that helps Percona MySQL DBAs run a PagerDuty shift: list open alerts, ack, rename, merge, and pull per-alert history (`stats`). It talks to the PagerDuty REST API over `httpx` and renders with `rich`. There is no server, database, or persisted state — every command is a fresh set of API calls.

## Commands

Use the venv interpreter explicitly (the README warns macOS `python`/`python3` is often too old; the project requires Python 3.10+):

```bash
./venv/bin/pip install -e .          # install / reinstall after dependency changes
./venv/bin/pip install -e ".[dev]"   # install with dev deps (pytest)
./venv/bin/pytest -q                 # run all tests
./venv/bin/pytest tests/test_parse.py -q          # one file
./venv/bin/pytest tests/test_parse.py::test_name  # one test
./venv/bin/pd <command>              # run the CLI (or alias `pd`)
```

There is no linter or formatter configured.

## Architecture

The flow is **CLI command → `PDClient` (API) → parse/transform helpers → display renderers**. `cli.py` is the only orchestrator; every other module is a focused, mostly-pure helper that is unit-tested in isolation.

- **`cli.py`** — all Click commands (`list`, `ack`, `rename`, `merge`, `stats`, `inspect`, `config-path`). Holds the cross-command helpers: `_team_ids` (resolve `--team`/`PD_TEAM_ID`), `_load_incidents`, `_ticket_context_for_incidents` (parallel metadata fetch via `ThreadPoolExecutor`, max 8 workers, one `PDClient` per worker), and `_warn_if_unscoped`. `main()` calls `ensure_utf8_stdio()` before dispatching.
- **`client.py`** — `PDClient` wraps every PagerDuty endpoint and raises `PDError` on any failure. Auth is `Token token=…`; write actions (ack/merge/rename) require a `From` email, resolved from `PD_FROM`/config or by calling `me()`. `resolve_incident_reference` is the key resolver: it accepts an `INC…` ServiceNow ticket, a PD incident number, or a PD incident id. INC lookups are **expensive** — they fetch per-incident metadata to find the ticket — which is why `stats` gates the slow team-history INC scan behind `--yes`.
- **`parse.py`** — pure string parsing of PagerDuty titles. Two distinct jobs: (1) extracting the ServiceNow `INC…` ticket from incident `metadata` (preferred) or `external_references`/linked records (fallback); (2) cleaning Percona/PMM alert titles. The `alert_signature(title, service) -> (customer, description)` pair is the **matching key** that groups recurring incidents in `stats` and is reused as the cleaned DESCRIPTION shown by `list`/`rename`. Customer comes from `[brackets]` in the title (or the service name). PMM "glued"/duplicated-host titles get special handling (`unglue_pmm_title`, `collapse_pmm_segments`, `title_has_pmm_merge_pattern`).
- **`stats.py`** — builds the per-alert history view: filters service history by `alert_signature`, computes the summary (counts, avg duration, typical fire window, last-seen), and renders. `STATS_WINDOW_DAYS` is the default window.
- **`merge.py`** — merge-specific logic: `customers_compatible` guard (refuses cross-customer merges), `choose_merge_title` (interactive title selection via `questionary`), and the labels/example text.
- **`display.py`** — `incident_row_from`, `sort_incident_rows`, `render_incident_table` for `pd list`.
- **`settings.py`** — config precedence. `config_value(name, env_names=…)` checks env vars first, then `~/.config/pd-shift/conf` (or `$XDG_CONFIG_HOME`). `KEY_ALIASES` maps several config keys to canonical names.
- **`console_io.py`** — shared `rich` Console factory and `ensure_utf8_stdio()` (reconfigures stdout/stderr to UTF-8 with `errors="replace"` to avoid macOS `UnicodeEncodeError`). `EMPTY = "-"` is the placeholder for missing fields.
- **`htmltext.py`**, **`timefmt.py`**, **`progress.py`** — strip HTML/ServiceNow URLs from notes, format relative/absolute UTC times, and render the single-line progress indicator used by `stats`.

## Conventions to preserve

- **Ticket terminology**: `INC…` always means a ServiceNow ticket parsed from PagerDuty metadata — never a PagerDuty id. PD incidents are identified by incident *number* or *id*. `resolve_incident_reference` handles all three forms; route new lookups through it.
- **Team scoping**: commands default to the configured team (`PD_TEAM_ID`/`team_id`). An unscoped run searches the whole account and is slow — call `_warn_if_unscoped` for any new command that lists incidents.
- **API-call cost matters**: INC resolution and per-incident metadata are N+1 calls. Reuse `_ticket_context_for_incidents` for batches and keep slow paths behind explicit flags (as `stats --yes` does).
- **Keep helpers pure and tested**: parsing/formatting/stats logic lives outside `cli.py` and has direct unit tests — add tests alongside (`tests/test_<module>.py`) rather than testing through the CLI.
