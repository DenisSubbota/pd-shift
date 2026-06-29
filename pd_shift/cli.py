#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

import click

from pd_shift.console_io import make_console

from pd_shift.client import PDClient, PDError
from pd_shift.display import incident_row_from, render_incident_table, sort_incident_rows
from pd_shift.merge import (
    choose_merge_title,
    customers_compatible,
    incident_customer,
    incident_display_title,
    incident_ticket_label,
    merge_example_text,
)
from pd_shift.parse import alert_signature, fixed_title_from_incident, incident_matches_signature, normalize_title
from pd_shift.progress import ProgressLine
from pd_shift.settings import config_path, config_value
from pd_shift.stats import (
    STATS_WINDOW_DAYS,
    build_stats_rows,
    print_inc_not_open_hint,
    render_stats,
    stats_alert_label,
    summarize_stats,
)

console = make_console()


def _team_ids(team: tuple[str, ...]) -> list[str]:
    ids = list(team)
    if not ids:
        env_team = config_value("team_id", env_names=("PD_TEAM_ID",))
        if env_team:
            ids.append(env_team)
    return ids


def _load_incidents(
    client: PDClient,
    *,
    mine: bool,
    team: tuple[str, ...],
) -> list[dict]:
    user_id = None
    if mine:
        user_id = client.me()["id"]

    team_ids = _team_ids(team)
    return client.list_open_incidents(user_id=user_id, team_ids=team_ids or None)


def _ticket_context_for_incidents(
    client: PDClient,
    incidents: list[dict],
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, dict]:
    results: dict[str, dict] = {}
    total = len(incidents)
    if total == 0:
        return results

    def fetch(incident_id: str) -> tuple[str, dict]:
        worker = PDClient(token=client.token, from_email=client.from_email)
        return incident_id, worker.incident_ticket_context(incident_id)

    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch, inc["id"]) for inc in incidents]
        for future in as_completed(futures):
            incident_id, context = future.result()
            results[incident_id] = context
            done += 1
            if on_progress:
                on_progress(done, total)

    return results


def _print_acked_incidents(client: PDClient, incidents: list[dict]) -> None:
    contexts = _ticket_context_for_incidents(client, incidents)
    console.print(f"[green]Acked {len(incidents)} incident(s):[/green]")
    for incident in incidents:
        context = contexts.get(incident["id"], {})
        label = incident_ticket_label(incident, context)
        description = incident_display_title(incident)
        console.print(f"  {label}  -  {description}")


def _warn_if_unscoped(mine: bool, team: tuple[str, ...]) -> None:
    if not mine and not _team_ids(team):
        console.print(
            "[yellow]warning:[/yellow] no PD_TEAM_ID / --team - listing all open incidents in account"
        )


@click.group()
@click.version_option(package_name="pd_shift")
def cli():
    """PagerDuty shift helper — list open alerts and ack triggered ones."""


@cli.command("config-path")
def config_path_cmd():
    """Print the config file path."""
    console.print(str(config_path()))


@cli.command("list")
@click.option(
    "--mine/--all",
    default=False,
    help="Only incidents assigned to you. Default: all open for team/account.",
)
@click.option(
    "--team",
    "team",
    multiple=True,
    help="Filter by team id (repeatable). Falls back to PD_TEAM_ID.",
)
@click.option(
    "-t",
    "--time",
    "show_time",
    is_flag=True,
    help="Show TRIGGERED column (relative time, or date if 7+ days ago).",
)
def list_cmd(mine: bool, team: tuple[str, ...], show_time: bool):
    """Show open alerts: INC - customer - description - host."""
    _warn_if_unscoped(mine, team)
    try:
        client = PDClient()
        incidents = _load_incidents(client, mine=mine, team=team)
    except PDError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    if not incidents:
        console.print("[dim]No open incidents.[/dim]")
        return

    console.print(f"[dim]{len(incidents)} open incident(s)[/dim]")
    context_by_id = _ticket_context_for_incidents(client, incidents)

    rows = []
    for incident in incidents:
        context = context_by_id.get(incident["id"], {})
        rows.append(
            incident_row_from(
                status=incident.get("status", ""),
                title=incident.get("title", ""),
                service=(incident.get("service") or {}).get("summary", ""),
                metadata=context.get("metadata"),
                linked_records=context.get("linked_records", []),
                created_at=incident.get("created_at"),
            )
        )

    render_incident_table(console, sort_incident_rows(rows, by_time=show_time), show_time=show_time)


@cli.command("ack")
@click.argument("ticket", required=False)
@click.option(
    "--mine/--all",
    default=False,
    help="Only ack incidents assigned to you. Default: all triggered for team/account.",
)
@click.option(
    "--team",
    "team",
    multiple=True,
    help="Filter by team id (repeatable). Falls back to PD_TEAM_ID.",
)
@click.option("--dry-run", is_flag=True, help="Print what would be acked without calling PD.")
def ack_cmd(ticket: str | None, mine: bool, team: tuple[str, ...], dry_run: bool):
    """Ack triggered incidents. Use `pd ack` for all, or `pd ack INC0011223` for one."""
    _warn_if_unscoped(mine, team)
    try:
        client = PDClient()
        incidents = _load_incidents(client, mine=mine, team=team)
    except PDError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    if ticket:
        incident = client.resolve_incident_reference(ticket, incidents)
        if not incident:
            console.print(f"[red]error:[/red] no open incident found for {ticket}")
            sys.exit(1)
        if incident.get("status") != "triggered":
            console.print(
                f"[yellow]skip:[/yellow] {ticket} is already {incident.get('status', 'unknown')}"
            )
            return
        to_ack = [incident]
    else:
        to_ack = [inc for inc in incidents if inc.get("status") == "triggered"]
        if not to_ack:
            console.print("[dim]No triggered incidents to ack.[/dim]")
            return

    if dry_run:
        console.print(f"[yellow]dry-run:[/yellow] would ack {len(to_ack)} incident(s):")
        for incident in to_ack:
            context = client.incident_ticket_context(incident["id"])
            label = incident_ticket_label(incident, context)
            description = incident_display_title(incident)
            console.print(f"  {label}  -  {description}")
        return

    try:
        client.ack_incidents([inc["id"] for inc in to_ack])
    except PDError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    _print_acked_incidents(client, to_ack)


@cli.command("rename")
@click.argument("ticket")
@click.option(
    "-d",
    "--description",
    default=None,
    help="Set full title explicitly. Without -d, auto-fix: strip Percona_MS_* noise, keep list DESCRIPTION.",
)
@click.option(
    "--mine/--all",
    default=False,
    help="Search only incidents assigned to you. Default: all open for team/account.",
)
@click.option(
    "--team",
    "team",
    multiple=True,
    help="Filter by team id (repeatable). Falls back to PD_TEAM_ID.",
)
@click.option("--dry-run", is_flag=True, help="Show old/new title without calling PD.")
def rename_cmd(
    ticket: str,
    description: str | None,
    mine: bool,
    team: tuple[str, ...],
    dry_run: bool,
):
    """Rename an open incident by INC ticket (or PD number)."""
    _warn_if_unscoped(mine, team)
    try:
        client = PDClient()
        incidents = _load_incidents(client, mine=mine, team=team)
        incident = client.resolve_incident_reference(ticket, incidents)
    except PDError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    if not incident:
        console.print(f"[red]error:[/red] no open incident found for {ticket}")
        sys.exit(1)

    old_title = normalize_title(incident.get("title", ""))
    service = (incident.get("service") or {}).get("summary", "")
    if description is not None:
        new_title = normalize_title(description)
    else:
        new_title = fixed_title_from_incident(incident.get("title", ""), service)

    if not new_title:
        console.print("[red]error:[/red] resulting title is empty")
        sys.exit(1)

    if new_title == old_title:
        console.print(f"[dim]{ticket} title already matches list view - no change[/dim]")
        return

    if dry_run:
        console.print(f"[yellow]dry-run:[/yellow] {ticket}")
        console.print(f"  old: {old_title}")
        console.print(f"  new: {new_title}")
        return

    try:
        client.rename_incident(incident["id"], new_title)
    except PDError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    console.print(f"[green]Renamed {ticket}.[/green]")
    console.print(f"  {new_title}")


@cli.command("merge")
@click.argument("parent_ticket", required=False)
@click.argument("source_ticket", required=False)
@click.option(
    "-t",
    "--title",
    default=None,
    help="Merged title (skips interactive prompts).",
)
@click.option(
    "--mine/--all",
    default=False,
    help="Search only incidents assigned to you. Default: all open for team/account.",
)
@click.option(
    "--team",
    "team",
    multiple=True,
    help="Filter by team id (repeatable). Falls back to PD_TEAM_ID.",
)
@click.option("--dry-run", is_flag=True, help="Show plan without calling PD.")
@click.option("--example", is_flag=True, help="Show example merge session.")
def merge_cmd(
    parent_ticket: str | None,
    source_ticket: str | None,
    title: str | None,
    mine: bool,
    team: tuple[str, ...],
    dry_run: bool,
    example: bool,
):
    """Merge two open incidents (same customer only). Parent keeps open."""
    if example:
        console.print(merge_example_text())
        return

    if not parent_ticket or not source_ticket:
        raise click.UsageError("Missing arguments: PARENT_TICKET SOURCE_TICKET")

    _warn_if_unscoped(mine, team)
    try:
        client = PDClient()
        incidents = _load_incidents(client, mine=mine, team=team)
        parent = client.resolve_incident_reference(parent_ticket, incidents)
        source = client.resolve_incident_reference(source_ticket, incidents)
    except PDError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    if not parent:
        console.print(f"[red]error:[/red] no open incident found for {parent_ticket}")
        sys.exit(1)
    if not source:
        console.print(f"[red]error:[/red] no open incident found for {source_ticket}")
        sys.exit(1)
    if parent["id"] == source["id"]:
        console.print("[red]error:[/red] cannot merge an incident into itself")
        sys.exit(1)

    parent_ctx = client.incident_ticket_context(parent["id"])
    source_ctx = client.incident_ticket_context(source["id"])
    parent_label = incident_ticket_label(parent, parent_ctx)
    source_label = incident_ticket_label(source, source_ctx)

    parent_customer = incident_customer(parent)
    source_customer = incident_customer(source)
    if not customers_compatible(parent_customer, source_customer):
        console.print(
            f"[red]error:[/red] cannot merge different customers: "
            f"{parent_customer} vs {source_customer}"
        )
        sys.exit(1)

    try:
        final_title = choose_merge_title(
            parent=parent,
            source=source,
            parent_label=parent_label,
            source_label=source_label,
            preset_title=title,
        )
    except click.Abort:
        console.print("[dim]Merge cancelled.[/dim]")
        sys.exit(1)
    if not final_title:
        console.print("[red]error:[/red] merged title cannot be empty")
        sys.exit(1)

    if dry_run:
        console.print(f"[yellow]dry-run:[/yellow] merge {source_label} -> {parent_label}")
        console.print(f"  customer: {parent_customer}")
        console.print(f"  title:    {final_title}")
        return

    try:
        client.merge_incidents(parent["id"], [source["id"]])
        client.rename_incident(parent["id"], final_title)
    except PDError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    console.print(f"[green]Merged {source_label} into {parent_label}[/green]")
    console.print(f"  Title: {final_title}")


@cli.command("inspect")
@click.argument("incident_id")
def inspect_cmd(incident_id: str):
    """Dump raw API fields for one incident (debug INC / linked records)."""
    try:
        client = PDClient()
        payload = client.inspect_incident(incident_id)
    except PDError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    console.print_json(data=payload)


STATS_MAX_DAYS = 180


@cli.command("stats")
@click.argument("ticket")
@click.option(
    "--days",
    default=STATS_WINDOW_DAYS,
    show_default=True,
    type=click.IntRange(1, STATS_MAX_DAYS),
    help="History window in days (PagerDuty allows up to 180 per request).",
)
@click.option(
    "--no-notes",
    is_flag=True,
    help="Skip fetching incident notes (default: include notes).",
)
@click.option(
    "--mine/--all",
    default=False,
    help="When resolving the ticket, prefer incidents assigned to you.",
)
@click.option(
    "--team",
    "team",
    multiple=True,
    help="Filter history by team id (repeatable). Falls back to PD_TEAM_ID.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Allow slow team-history search by INC when the alert is not open.",
)
def stats_cmd(
    ticket: str,
    days: int,
    no_notes: bool,
    mine: bool,
    team: tuple[str, ...],
    yes: bool,
):
    """Show alert history for one incident (same customer + description signature)."""
    show_notes = not no_notes
    _warn_if_unscoped(mine, team)
    progress = ProgressLine(console)
    try:
        progress.update("Connecting to PagerDuty...")
        client = PDClient()
        team_ids = _team_ids(team) or None

        progress.update("Loading open incidents...")
        open_incidents = _load_incidents(client, mine=mine, team=team)

        def on_resolve(message: str) -> None:
            progress.update(message)

        reference = client.resolve_incident_reference(ticket, open_incidents)
        if not reference and ticket.strip().upper().startswith("INC") and not yes:
            progress.done()
            print_inc_not_open_hint(console, ticket.strip().upper())
            sys.exit(1)

        if not reference:
            reference = client.resolve_reference_in_team_history(
                ticket,
                team_ids=team_ids,
                days=days,
                on_progress=on_resolve,
            )
        if not reference:
            progress.done()
            console.print(
                f"[red]error:[/red] no incident found for {ticket} in the last {days} days"
            )
            sys.exit(1)

        service = reference.get("service") or {}
        customer, signature = alert_signature(
            reference.get("title", ""), service.get("summary", "")
        )
        alert_label = stats_alert_label(customer, signature)
        progress.update(f"Identified -> {alert_label}")

        def on_service_history(page: int, loaded: int, more: bool, scope: str) -> None:
            suffix = "+" if more else "done"
            progress.update(
                f"{alert_label}: fetching {scope}... page {page} ({loaded} incidents, {suffix})"
            )

        historical = client.stats_history_for_reference(
            reference,
            team_ids=team_ids,
            days=days,
            on_page=on_service_history,
        )

        progress.update(f"{alert_label}: matching in {len(historical)} service incidents...")
        matched = [
            incident
            for incident in historical
            if incident_matches_signature(incident, customer, signature)
        ]

        def on_metadata(done: int, total: int) -> None:
            progress.update(f"{alert_label}: loading INC metadata... {done}/{total}")

        contexts = _ticket_context_for_incidents(client, matched, on_progress=on_metadata)
        ref_context = contexts.get(reference["id"]) or client.incident_ticket_context(reference["id"])
        reference_label = incident_ticket_label(reference, ref_context)

        notes_by_id: dict[str, list[dict]] | None = None
        if show_notes:
            notes_by_id = {}
            for index, incident in enumerate(matched, start=1):
                progress.update(f"{alert_label}: fetching notes... {index}/{len(matched)}")
                notes_by_id[incident["id"]] = client.incident_notes(incident["id"])

        progress.done()

        rows = build_stats_rows(matched, contexts=contexts, notes_by_id=notes_by_id)
        summary = summarize_stats(rows, reference_incident=reference, window_days=days)
        render_stats(
            console,
            signature=signature,
            customer=customer,
            reference_label=reference_label,
            rows=rows,
            summary=summary,
            show_notes=show_notes,
            window_days=days,
        )
    except KeyboardInterrupt:
        progress.done()
        console.print("\n[dim]Cancelled.[/dim]")
        sys.exit(130)
    except PDError as exc:
        progress.done()
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(1)
    finally:
        progress.done()


def main():
    from pd_shift.console_io import ensure_utf8_stdio

    ensure_utf8_stdio()
    cli()


if __name__ == "__main__":
    main()
