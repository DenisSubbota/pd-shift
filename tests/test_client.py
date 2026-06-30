from pd_shift.client import PDClient, PDError, _ticket_context_from_payload


class FakeClient(PDClient):
    def __init__(self):
        pass

    def incident_ticket_context(self, incident_id: str) -> dict:
        contexts = {
            "PONE": {
                "metadata": {
                    "servicenow_itsm_example_INC0011223": (
                        '{"external_name":"INC0011223 (example)"}'
                    )
                },
                "linked_records": [],
            },
            "PTWO": {"metadata": {}, "linked_records": []},
        }
        return contexts[incident_id]


def test_resolve_incident_by_inc():
    client = FakeClient()
    incidents = [
        {"id": "PONE", "incident_number": 101, "status": "triggered"},
        {"id": "PTWO", "incident_number": 102, "status": "triggered"},
    ]
    found = client.resolve_incident_reference("INC0011223", incidents)
    assert found is not None
    assert found["id"] == "PONE"


def test_resolve_incident_by_pd_number():
    client = FakeClient()
    incidents = [
        {"id": "PONE", "incident_number": 101, "status": "triggered"},
        {"id": "PTWO", "incident_number": 102, "status": "triggered"},
    ]
    found = client.resolve_incident_reference("102", incidents)
    assert found is not None
    assert found["id"] == "PTWO"


def test_request_wraps_connection_errors(monkeypatch):
    import httpx

    client = PDClient.__new__(PDClient)
    client.token = "test-token"
    client.from_email = None
    client._me_cache = None
    client.MAX_RETRIES = 0
    client._request_lock = __import__("threading").Lock()

    class FailingClient:
        def request(self, *args, **kwargs):
            raise httpx.ConnectError("TLS failed")

        def close(self):
            pass

    client._client = FailingClient()

    try:
        client._request("GET", "/incidents")
        raise AssertionError("expected PDError")
    except PDError as exc:
        assert "PagerDuty connection failed" in str(exc)


def test_ticket_context_from_payload_resolves_linked_records():
    payload = {
        "incident": {
            "metadata": {"servicenow_itsm_example_INC0011223": "{}"},
            "external_references": [{"id": "REF1", "summary": "INC0011223"}],
        },
        "external_references": [
            {"id": "REF1", "summary": "INC0011223", "external_name": "INC0011223"},
        ],
    }
    context = _ticket_context_from_payload(payload)
    assert context["metadata"]["servicenow_itsm_example_INC0011223"] == "{}"
    assert context["linked_records"][0]["external_name"] == "INC0011223"


def test_incident_details_includes_notes_when_requested(monkeypatch):
    client = PDClient.__new__(PDClient)
    client.token = "test-token"
    client.from_email = None
    client._me_cache = None
    client._request_lock = __import__("threading").Lock()
    client._client = object()

    calls: list[list[str]] = []

    def fake_get_incident(incident_id: str, *, includes: list[str] | None = None):
        calls.append(list(includes or []))
        return {
            "incident": {"metadata": {}},
            "notes": [{"content": "hello"}],
        }

    monkeypatch.setattr(client, "get_incident", fake_get_incident)
    monkeypatch.setattr(client, "incident_log_entries", lambda *args, **kwargs: [])

    without_notes = client.incident_details("PID1", include_notes=False)
    with_notes = client.incident_details("PID1", include_notes=True)

    assert "notes" not in without_notes
    assert "notes" not in calls[0]
    assert with_notes["notes"] == [{"content": "hello"}]
    assert "notes" in calls[1]


def test_incident_details_falls_back_to_log_entry_notes(monkeypatch):
    from pd_shift.client import PDClient

    client = PDClient.__new__(PDClient)
    client.token = "test-token"
    client.from_email = None
    client._me_cache = None
    client._request_lock = __import__("threading").Lock()
    client._client = object()

    def fake_get_incident(incident_id: str, *, includes: list[str] | None = None):
        return {"incident": {"metadata": {}}, "notes": []}

    def fake_log_entries(incident_id: str, *, limit: int = 20):
        return [
            {
                "type": "annotate_log_entry",
                "created_at": "2026-06-30T07:00:00Z",
                "channel": {
                    "type": "note",
                    "summary": "(from ServiceNow:jdoe) [code]<p>found PRB0044556</p>",
                },
            }
        ]

    monkeypatch.setattr(client, "get_incident", fake_get_incident)
    monkeypatch.setattr(client, "incident_log_entries", fake_log_entries)

    details = client.incident_details("PID1", include_notes=True)
    assert len(details["notes"]) == 1
    assert "PRB0044556" in details["notes"][0]["content"]
