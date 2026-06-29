from pd_shift.client import PDClient


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
