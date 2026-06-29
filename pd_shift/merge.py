from __future__ import annotations

import sys

import click
import questionary

from pd_shift.console_io import EMPTY
from pd_shift.parse import (
    customer_from_title,
    fixed_title_from_incident,
    normalize_title,
    ticket_from_incident,
)

CUSTOM_TITLE = "__custom__"


def incident_customer(incident: dict) -> str:
    service = (incident.get("service") or {}).get("summary", "")
    return customer_from_title(incident.get("title", ""), service)


def incident_display_title(incident: dict) -> str:
    service = (incident.get("service") or {}).get("summary", "")
    return fixed_title_from_incident(incident.get("title", ""), service)


def incident_ticket_label(incident: dict, context: dict) -> str:
    ticket = ticket_from_incident(
        metadata=context.get("metadata"),
        linked_records=context.get("linked_records", []),
    )
    if ticket:
        return ticket
    number = incident.get("incident_number")
    return f"#{number}" if number else incident.get("id", "?")


def customers_compatible(customer_a: str, customer_b: str) -> bool:
    if customer_a == EMPTY or customer_b == EMPTY:
        return False
    return customer_a.casefold() == customer_b.casefold()


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _choose_title_with_arrows(
    *,
    parent_title: str,
    source_title: str,
    parent_label: str,
    source_label: str,
) -> str:
    selected = questionary.select(
        "Choose title for merged incident (↑/↓, Enter):",
        choices=[
            questionary.Choice(
                title=f"{parent_title}  [{parent_label}]",
                value=parent_title,
            ),
            questionary.Choice(
                title=f"{source_title}  [{source_label}]",
                value=source_title,
            ),
            questionary.Choice(title="Custom title...", value=CUSTOM_TITLE),
        ],
        use_indicator=True,
        use_shortcuts=False,
    ).ask()
    if selected is None:
        raise click.Abort()

    if selected == CUSTOM_TITLE:
        base = questionary.text("Custom title:").ask()
        if base is None:
            raise click.Abort()
        return base

    return selected


def _edit_title_in_place(base: str) -> str:
    edited = questionary.text(
        "Edit merged title (modify in place, add node names if needed):",
        default=base,
    ).ask()
    if edited is None:
        raise click.Abort()
    return edited


def _choose_title_fallback(
    *,
    parent_title: str,
    source_title: str,
    parent_label: str,
    source_label: str,
) -> str:
    click.echo("Choose title for merged incident:")
    click.echo(f"  1) {parent_title}  [{parent_label}]")
    click.echo(f"  2) {source_title}  [{source_label}]")
    click.echo("  3) Custom title")
    choice = click.prompt("Choice", type=click.Choice(["1", "2", "3"]), default="1")

    if choice == "1":
        return parent_title
    if choice == "2":
        return source_title
    return click.prompt("Custom title")


def choose_merge_title(
    *,
    parent: dict,
    source: dict,
    parent_label: str,
    source_label: str,
    preset_title: str | None,
) -> str | None:
    parent_title = incident_display_title(parent)
    source_title = incident_display_title(source)

    if preset_title is not None:
        return normalize_title(preset_title)

    click.echo()
    click.echo(f"Customer: {incident_customer(parent)}")
    click.echo(f"Merge {source_label} → {parent_label} (parent keeps open)")
    click.echo()

    if _is_interactive():
        base = _choose_title_with_arrows(
            parent_title=parent_title,
            source_title=source_title,
            parent_label=parent_label,
            source_label=source_label,
        )
        click.echo()
        edited = _edit_title_in_place(base)
    else:
        base = _choose_title_fallback(
            parent_title=parent_title,
            source_title=source_title,
            parent_label=parent_label,
            source_label=source_label,
        )
        click.echo()
        edited = click.prompt("Edit merged title", default=base)

    return normalize_title(edited)


def merge_example_text() -> str:
    return """
Example session:

  $ pd merge INC0011223 INC0044556

  Customer: Zephyr Labs
  Merge INC0044556 → INC0011223 (parent keeps open)

  Choose title for merged incident (↑/↓, Enter):
  ❯ Disk Space Low - zephyr-db-01  [INC0011223]
    Disk Space Low - zephyr-db-02  [INC0044556]
    Custom title...

  Edit merged title (modify in place, add node names if needed):
  Disk Space Low - zephyr-db-01, zephyr-db-02

  Merged INC0044556 into INC0011223
  Title: Disk Space Low - zephyr-db-01, zephyr-db-02

Different customers are blocked:

  $ pd merge INC0011223 INC0099999
  error: cannot merge different customers: Zephyr Labs vs Northwind LLC
""".strip()
