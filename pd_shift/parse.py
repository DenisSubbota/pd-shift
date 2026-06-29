import json
import re

INC_RE = re.compile(r"INC\d+", re.IGNORECASE)
CUSTOMER_RE = re.compile(r"\[([^\]]+)\]")
HOST_RE = re.compile(
    r"\b("
    r"rds-aurora[-\w]+|"
    r"rds[-\w]+|"
    r"[a-z0-9][-a-z0-9]*(?:\.[a-z0-9][-a-z0-9]*)+"
    r")\b",
    re.IGNORECASE,
)


def _inc_from_text(text: str) -> str | None:
    match = INC_RE.search(text)
    return match.group(0).upper() if match else None


def ticket_from_metadata(metadata: dict | None) -> str | None:
    """Percona ServiceNow links live in incident.metadata (Linked Records in PD UI)."""
    if not metadata:
        return None

    for key, value in metadata.items():
        ticket = _inc_from_text(str(key))
        if ticket:
            return ticket

        if not isinstance(value, str):
            continue

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None

        if isinstance(parsed, dict):
            for field in ("external_name", "external_id", "summary", "external_url"):
                ticket = _inc_from_text(str(parsed.get(field, "")))
                if ticket:
                    return ticket

        ticket = _inc_from_text(value)
        if ticket:
            return ticket

    return None


def ticket_from_linked_records(refs: list[dict]) -> str | None:
    """Fallback for accounts that expose ServiceNow via external_references."""
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        for field in (
            "external_name",
            "external_id",
            "summary",
            "external_url",
            "html_url",
            "self",
            "metadata_key",
            "metadata_value",
        ):
            ticket = _inc_from_text(str(ref.get(field, "")))
            if ticket:
                return ticket
    return None


def ticket_from_incident(*, metadata: dict | None, linked_records: list[dict]) -> str | None:
    return ticket_from_metadata(metadata) or ticket_from_linked_records(linked_records)


PERCONA_ALERT_NOISE_RE = re.compile(
    r"^(?:Gascan\s*-\s*)?"
    r"Percona_?MS_[A-Za-z0-9_]+\s*-\s*"
    r"(?:CRITICAL|WARNING|WARN)\s*-\s*",
    re.IGNORECASE,
)
GASCAN_SEGMENT_RE = re.compile(r"^gascan$", re.IGNORECASE)


def clean_customer(name: str) -> str:
    name = normalize_title(name)
    if not name:
        return "—"

    parts = [part.strip() for part in name.split(" - ") if part.strip()]
    parts = [part for part in parts if not GASCAN_SEGMENT_RE.match(part)]
    if parts:
        return parts[0]
    return name


def clean_description(text: str) -> str:
    text = normalize_title(text)
    text = PERCONA_ALERT_NOISE_RE.sub("", text)
    text = re.sub(r"^Gascan\s*-\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text).strip(" -|")
    return text or "—"


def normalize_title(title: str) -> str:
    return " ".join(title.split())


def customer_from_title(title: str, service: str = "") -> str:
    title = normalize_title(title)
    match = CUSTOMER_RE.search(title)
    if match:
        return clean_customer(match.group(1))
    if service:
        return clean_customer(service.strip())
    return "—"


def host_from_title(title: str) -> str | None:
    title = normalize_title(title)
    for pattern in (
        re.compile(r"\brds-aurora[-\w]+", re.IGNORECASE),
        re.compile(r"\brds[-\w]+", re.IGNORECASE),
        re.compile(r"[-\w]+-blast[-\w]+", re.IGNORECASE),
        HOST_RE,
    ):
        match = pattern.search(title)
        if match:
            return match.group(0)
    parts = [part.strip() for part in title.split(" - ") if part.strip()]
    if len(parts) >= 2:
        return parts[-1]
    return None


def description_from_title(title: str, customer: str, host: str | None = None) -> str:
    del customer, host  # kept for call-site compatibility; host stays in description text
    parts = [part.strip() for part in normalize_title(title).split(" - ") if part.strip()]
    parts = [part for part in parts if not GASCAN_SEGMENT_RE.match(part)]
    return clean_description(" - ".join(parts))


def fixed_title_from_incident(title: str, service: str = "") -> str:
    """Title after auto-fix: same text as the DESCRIPTION column in `pd list`."""
    customer = customer_from_title(title, service)
    host = host_from_title(title)
    return description_from_title(title, customer, host)


def alert_signature(title: str, service: str = "") -> tuple[str, str]:
    """Customer + normalized alert description used to match recurring incidents."""
    customer = customer_from_title(title, service)
    host = host_from_title(title)
    signature = description_from_title(title, customer, host)
    return customer, signature


def incident_matches_signature(incident: dict, customer: str, signature: str) -> bool:
    service = (incident.get("service") or {}).get("summary", "")
    inc_customer, inc_signature = alert_signature(incident.get("title", ""), service)
    return inc_customer == customer and inc_signature == signature


def format_line(
    *,
    ticket: str | None,
    customer: str,
    description: str,
) -> str:
    ticket_part = ticket or "—"
    return f"{ticket_part} - {customer} - {description}"
