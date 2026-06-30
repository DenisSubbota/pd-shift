from pd_shift.parse import (
    alert_signature,
    customer_from_title,
    description_from_title,
    display_title_differs_from_pd,
    fixed_title_from_incident,
    format_line,
    host_from_title,
    incident_matches_signature,
    strip_snow_refs,
    ticket_from_incident,
    ticket_from_linked_records,
    ticket_from_metadata,
    title_has_pmm_merge_pattern,
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


PROXYSQL_GLUED = (
    "ProxySQL Host Group Does Not Have Online Server - proxysql-1"
    "Percona_MS_HostGroupDoesNotHaveOnlineServer - CRITICAL - "
    "ProxySQL Host Group Does Not Have Online Server - proxysql-2"
)
MYSQL_READONLY_GLUED = (
    "MySQL Primary ReadOnly - db-02-mysql"
    "Percona_MS_MySQLPrimaryReadOnly - CRITICAL - "
    "MySQL Primary ReadOnly - db-03-mysql"
)


def test_pmm_glued_proxysql_title():
    assert description_from_title(PROXYSQL_GLUED, "", None) == (
        "ProxySQL Host Group Does Not Have Online Server - proxysql-1, proxysql-2"
    )
    assert fixed_title_from_incident(PROXYSQL_GLUED) == (
        "ProxySQL Host Group Does Not Have Online Server - proxysql-1, proxysql-2"
    )
    assert title_has_pmm_merge_pattern(PROXYSQL_GLUED)


def test_pmm_glued_mysql_readonly_title():
    assert description_from_title(MYSQL_READONLY_GLUED, "", None) == (
        "MySQL Primary ReadOnly - db-02-mysql, db-03-mysql"
    )
    assert title_has_pmm_merge_pattern(MYSQL_READONLY_GLUED)


def test_normal_percona_title_not_flagged_for_rename():
    assert not title_has_pmm_merge_pattern(SAMPLE_TITLE)


def test_display_differs_when_percona_noise_in_pd_title():
    assert display_title_differs_from_pd(SAMPLE_TITLE)
    assert not display_title_differs_from_pd("Disk Space Low - zephyr-db-01")


def test_display_differs_for_pmm_glued_title():
    assert display_title_differs_from_pd(PROXYSQL_GLUED)


MYSQL_HISTORY_LENGTH = (
    "Percona_MS_MySQLHistoryLength_HighThr Alerting Rule - CRITICAL - "
    "MySQL InnoDB History List Length - db-01-mysql"
)


def test_strip_snow_refs_removes_bracketed_problem():
    assert (
        strip_snow_refs("Disk Space Low - zephyr-db-01 ( PRB0044556 )")
        == "Disk Space Low - zephyr-db-01"
    )


def test_strip_snow_refs_removes_all_types_and_bare_tokens():
    assert strip_snow_refs("Foo INC0011223 bar [CHG0011223] baz TASK0011223") == "Foo bar baz"


def test_strip_snow_refs_keeps_hostlike_short_digits():
    # "task5" is not a SNOW ref (needs >=4 digits) and must survive.
    assert strip_snow_refs("MySQL Down - my-task5-host") == "MySQL Down - my-task5-host"


def test_list_description_keeps_problem_ref():
    # The PRB in brackets is a human-added annotation; pd list must show it.
    title = "Disk Space Low - zephyr-db-01 ( PRB0044556 )"
    assert description_from_title(title, "Zephyr Labs", None) == (
        "Disk Space Low - zephyr-db-01 ( PRB0044556 )"
    )


def test_signature_excludes_problem_ref():
    # The matching key drops the annotation so tagged/untagged alerts group.
    title = "Disk Space Low - zephyr-db-01 ( PRB0044556 )"
    _customer, signature = alert_signature(title, "Zephyr Labs")
    assert signature == "Disk Space Low - zephyr-db-01"


def test_list_description_unflagged_for_problem_ref():
    # A PRB annotation alone must not make the row look like it needs a rename.
    assert not display_title_differs_from_pd("Disk Space Low - zephyr-db-01 ( PRB0044556 )")


def test_signature_matches_regardless_of_problem_ref():
    service = "Zephyr Labs"
    tagged = {
        "title": "Disk Space Low - zephyr-db-01 ( PRB0044556 )",
        "service": {"summary": service},
    }
    untagged_title = "Disk Space Low - zephyr-db-01"
    customer, signature = alert_signature(untagged_title, service)
    assert incident_matches_signature(tagged, customer, signature)


def test_customer_skips_problem_only_bracket():
    title = "Disk Space Low - zephyr-db-01 [PRB0044556]"
    assert customer_from_title(title, "Zephyr Labs") == "Zephyr Labs"


def test_alerting_rule_title_strips_for_stats_match():
    service = "Acme Corp - Gascan"
    clean = "MySQL InnoDB History List Length - db-01-mysql"
    assert description_from_title(MYSQL_HISTORY_LENGTH, service, None) == clean
    ref_customer, ref_signature = alert_signature(clean, service)
    incident = {
        "title": MYSQL_HISTORY_LENGTH,
        "service": {"summary": service},
    }
    assert incident_matches_signature(incident, ref_customer, ref_signature)
