import json
import re

from pd_shift.console_io import EMPTY

INC_RE = re.compile(r"INC\d+", re.IGNORECASE)
CUSTOMER_RE = re.compile(r"\[([^\]]+)\]")
# ServiceNow references that ride along in alert titles. They are noise in the
# DESCRIPTION/signature: INC is the row's own ticket (resolved from metadata),
# and PRB/CHG/TASK are unrelated record ids. >=4 digits so a real ref
# (e.g. PRB0044556) matches but a hostlike token (e.g. "task5") does not.
SNOW_REF_RE = re.compile(r"\b(?:INC|PRB|CHG|TASK)\d{4,}\b", re.IGNORECASE)
SNOW_REF_BRACKETED_RE = re.compile(
    r"[\(\[]\s*(?:INC|PRB|CHG|TASK)\d{4,}\s*[\)\]]",
    re.IGNORECASE,
)
EMPTY_BRACKETS_RE = re.compile(r"[\(\[]\s*[\)\]]")
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


PERCONA_RULE_SUFFIX = r"(?:\s+Alerting Rule)?"
PERCONA_ALERT_NOISE_RE = re.compile(
    r"^(?:Gascan\s*-\s*)?"
    rf"Percona_?MS_[A-Za-z0-9_]+{PERCONA_RULE_SUFFIX}\s*-\s*"
    r"(?:CRITICAL|WARNING|WARN)\s*-\s*",
    re.IGNORECASE,
)
PERCONA_MS_BLOCK_ANYWHERE_RE = re.compile(
    rf"Percona_?MS_[A-Za-z0-9_]+{PERCONA_RULE_SUFFIX}\s*-\s*(?:CRITICAL|WARNING|WARN)\s*-\s*",
    re.IGNORECASE,
)
GLUED_BEFORE_PERCONA_RE = re.compile(r"(?<=[\w.-])(Percona_?MS_)", re.IGNORECASE)
GASCAN_SEGMENT_RE = re.compile(r"^gascan$", re.IGNORECASE)


def clean_customer(name: str) -> str:
    name = normalize_title(name)
    if not name:
        return EMPTY

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
    return text or EMPTY


def normalize_title(title: str) -> str:
    return " ".join(title.split())


def strip_snow_refs(text: str) -> str:
    """Remove ServiceNow refs (INC/PRB/CHG/TASK) and any now-empty brackets."""
    text = SNOW_REF_BRACKETED_RE.sub(" ", text)
    text = SNOW_REF_RE.sub(" ", text)
    text = EMPTY_BRACKETS_RE.sub(" ", text)
    return re.sub(r"\s{2,}", " ", text).strip(" -|")


def customer_from_title(title: str, service: str = "") -> str:
    title = normalize_title(title)
    for match in CUSTOMER_RE.finditer(title):
        # Skip a bracket that is only a SNOW ref (e.g. "[PRB0044556]").
        if strip_snow_refs(match.group(1)):
            return clean_customer(match.group(1))
    if service:
        return clean_customer(service.strip())
    return EMPTY


def unglue_pmm_title(title: str) -> str:
    """PMM sometimes glues the host onto Percona_MS_ with no separator."""
    return GLUED_BEFORE_PERCONA_RE.sub(r" - \1", normalize_title(title))


def _alert_segments_from_title(title: str) -> list[str]:
    title = unglue_pmm_title(title)
    segments = [
        part.strip(" -|")
        for part in PERCONA_MS_BLOCK_ANYWHERE_RE.split(title)
        if part.strip(" -|")
    ]
    return segments


def _segment_desc_host(segment: str) -> tuple[str, str | None]:
    parts = [part.strip() for part in segment.split(" - ") if part.strip()]
    parts = [part for part in parts if not GASCAN_SEGMENT_RE.match(part)]
    if len(parts) >= 2:
        return " - ".join(parts[:-1]), parts[-1]
    return clean_description(segment), None


def collapse_pmm_segments(segments: list[str]) -> str:
    if not segments:
        return EMPTY
    if len(segments) == 1:
        return clean_description(segments[0])

    parsed = [_segment_desc_host(segment) for segment in segments]
    descriptions = [desc for desc, _host in parsed]
    hosts = [host for _desc, host in parsed if host]

    if len(set(descriptions)) == 1 and hosts:
        return f"{descriptions[0]} - {', '.join(hosts)}"

    return clean_description(" - ".join(segments))


def normalize_pmm_title(title: str) -> str:
    return collapse_pmm_segments(_alert_segments_from_title(title))


def title_has_pmm_merge_pattern(title: str) -> bool:
    """True when PMM likely glued or duplicated hosts in the PD title."""
    raw = normalize_title(title)
    if GLUED_BEFORE_PERCONA_RE.search(raw):
        return True
    segments = _alert_segments_from_title(raw)
    if len(segments) < 2:
        return False
    descriptions = [_segment_desc_host(segment)[0] for segment in segments]
    return len(set(descriptions)) == 1


def host_from_title(title: str) -> str | None:
    title = normalize_pmm_title(title)
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
    # SNOW refs (e.g. a human-added "( PRB0043110 )") are kept here so pd list
    # shows them; they are stripped only for the stats matching key (alert_signature).
    return normalize_pmm_title(title)


def fixed_title_from_incident(title: str, service: str = "") -> str:
    """Title after auto-fix: same text as the DESCRIPTION column in `pd list`."""
    customer = customer_from_title(title, service)
    host = host_from_title(title)
    return description_from_title(title, customer, host)


def display_title_differs_from_pd(title: str, service: str = "") -> bool:
    """True when the cleaned DESCRIPTION column would not match the PD incident title."""
    raw = normalize_title(title)
    if not raw:
        return False
    return fixed_title_from_incident(title, service) != raw


def alert_signature(title: str, service: str = "") -> tuple[str, str]:
    """Customer + normalized alert description used to match recurring incidents."""
    customer = customer_from_title(title, service)
    host = host_from_title(title)
    signature = strip_snow_refs(description_from_title(title, customer, host)) or EMPTY
    return customer, signature


def incident_matches_signature(incident: dict, customer: str, signature: str) -> bool:
    """Match recurring alerts by normalized description (alert + host), not customer."""
    del customer  # kept for call-site compatibility; stats groups by description only
    service = (incident.get("service") or {}).get("summary", "")
    _inc_customer, inc_signature = alert_signature(incident.get("title", ""), service)
    return inc_signature == signature


def format_line(
    *,
    ticket: str | None,
    customer: str,
    description: str,
) -> str:
    ticket_part = ticket or EMPTY
    return f"{ticket_part} - {customer} - {description}"
