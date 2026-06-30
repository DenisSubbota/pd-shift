from click.testing import CliRunner

from pd_shift.cli import _format_incident_summary, cli


def test_format_incident_summary_includes_customer():
    incident = {
        "title": "Percona_MS_Sample - CRITICAL - IP Controller - prod2",
        "service": {"summary": "Acme Corp - Gascan"},
        "incident_number": 123,
    }
    context = {
        "metadata": {
            "servicenow_itsm_example_INC0011223": "{}",
        },
        "linked_records": [],
    }
    line = _format_incident_summary(incident, context)
    assert line == "INC0011223 - Acme Corp - IP Controller - prod2"


def test_resolve_incidents_builds_resolved_payload(monkeypatch):
    from pd_shift.client import PDClient

    client = PDClient.__new__(PDClient)
    client.from_email = "dba@example.com"
    captured: dict = {}

    def fake_request(method, path, *, write=False, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["write"] = write
        captured["body"] = kwargs.get("json")
        return {}

    monkeypatch.setattr(client, "_prepare_write", lambda: None)
    monkeypatch.setattr(client, "_request", fake_request)

    client.resolve_incidents(["PID1", "PID2"])

    assert captured["method"] == "PUT"
    assert captured["path"] == "/incidents"
    assert captured["write"] is True
    assert captured["body"] == {
        "incidents": [
            {"id": "PID1", "type": "incident_reference", "status": "resolved"},
            {"id": "PID2", "type": "incident_reference", "status": "resolved"},
        ]
    }


def test_help_lists_all_commands_without_truncation():
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    # Isolate the "Commands:" block (the usage line legitimately ends "[ARGS]...").
    command_block = result.output.split("Commands:", 1)[1]
    # Click truncates long command summaries with "..."; each command gets an
    # explicit short_help so the listing stays complete and readable.
    assert "..." not in command_block
    for name in (
        "ack",
        "config-path",
        "inspect",
        "list",
        "merge",
        "rename",
        "resolve",
        "stats",
    ):
        assert name in command_block


def test_resolve_requires_inc_ticket():
    result = CliRunner().invoke(cli, ["resolve", "123456"])
    assert result.exit_code == 1
    assert "requires a ServiceNow INC ticket" in result.output


def test_add_incident_note_posts_content(monkeypatch):
    from pd_shift.client import PDClient

    client = PDClient.__new__(PDClient)
    captured: dict = {}

    def fake_request(method, path, *, write=False, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = kwargs.get("json")
        return {}

    monkeypatch.setattr(client, "_prepare_write", lambda: None)
    monkeypatch.setattr(client, "_request", fake_request)

    client.add_incident_note("PID1", "  fixed replication  ")

    assert captured["method"] == "POST"
    assert captured["path"] == "/incidents/PID1/notes"
    assert captured["body"] == {"note": {"content": "fixed replication"}}


def test_resolve_incident_adds_note_after_resolve(monkeypatch):
    from pd_shift.client import PDClient

    client = PDClient.__new__(PDClient)
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        client,
        "resolve_incidents",
        lambda incident_ids: calls.append(("resolve", incident_ids[0])),
    )
    monkeypatch.setattr(
        client,
        "add_incident_note",
        lambda incident_id, content: calls.append(("note", content)),
    )

    client.resolve_incident("PID1", note="all good")
    assert calls == [("resolve", "PID1"), ("note", "all good")]
