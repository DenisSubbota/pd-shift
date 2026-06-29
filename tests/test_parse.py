from pd_shift.parse import (
    customer_from_title,
    description_from_title,
    fixed_title_from_incident,
    format_line,
    host_from_title,
    ticket_from_incident,
    ticket_from_linked_records,
    ticket_from_metadata,
)

SAMPLE_METADATA = {
    "servicenow_itsm_example_INC0011223": (
        '{"external_name":"INC0011223 (example)",'
        '"external_url":"https://sn.example.invalid/incident.do?sys_id=abc123def456"}'
    )
}

SAMPLE_TITLE = (
    "Percona_MS_SampleAlertRule - CRITICAL - Disk Space Low - zephyr-db-01"
)


def test_ticket_from_metadata_json():
    assert ticket_from_metadata(SAMPLE_METADATA) == "INC0011223"


def test_ticket_from_metadata_key_only():
    assert ticket_from_metadata({"servicenow_itsm_example_INC0099887": "{}"}) == "INC0099887"


def test_ticket_from_linked_records_external_name():
    refs = [{"external_name": "INC0044556", "external_url": "https://sn.example.invalid/"}]
    assert ticket_from_linked_records(refs) == "INC0044556"


def test_ticket_from_incident_prefers_metadata():
    refs = [{"external_name": "INC0000001"}]
    assert ticket_from_incident(metadata=SAMPLE_METADATA, linked_records=refs) == "INC0011223"


def test_customer_from_brackets():
    title = "[Northwind LLC] Low Free Memory on rds-aurora-staging-9"
    assert customer_from_title(title) == "Northwind LLC"


def test_customer_strips_gascan_from_service():
    assert customer_from_title("", "Zephyr Labs - Gascan") == "Zephyr Labs"


def test_host_from_alert_title():
    assert host_from_title(SAMPLE_TITLE) == "zephyr-db-01"


def test_description_strips_alert_noise_keeps_host():
    host = host_from_title(SAMPLE_TITLE)
    desc = description_from_title(SAMPLE_TITLE, "Zephyr Labs", host)
    assert desc == "Disk Space Low - zephyr-db-01"


def test_fixed_title_from_incident():
    title = "Percona_MS_SampleAlertRule - CRITICAL - Disk Space Low - zephyr-db-01"
    assert fixed_title_from_incident(title) == "Disk Space Low - zephyr-db-01"


def test_format_line_example():
    host = host_from_title(SAMPLE_TITLE)
    line = format_line(
        ticket="INC0011223",
        customer=customer_from_title(SAMPLE_TITLE, "Zephyr Labs - Gascan"),
        description=description_from_title(SAMPLE_TITLE, "Zephyr Labs", host),
    )
    assert line == "INC0011223 - Zephyr Labs - Disk Space Low - zephyr-db-01"
