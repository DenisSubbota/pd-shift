from pd_shift.merge import customers_compatible, incident_customer


def test_customers_compatible():
    assert customers_compatible("Zephyr Labs", "Zephyr Labs")
    assert customers_compatible("Zephyr Labs", "zephyr labs")
    assert not customers_compatible("Zephyr Labs", "Northwind LLC")
    assert not customers_compatible("—", "Zephyr Labs")


def test_incident_customer_from_service():
    incident = {
        "title": "Percona_MS_SampleAlertRule - CRITICAL - Disk Space Low - zephyr-db-01",
        "service": {"summary": "Zephyr Labs - Gascan"},
    }
    assert incident_customer(incident) == "Zephyr Labs"
