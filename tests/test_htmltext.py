from pd_shift.htmltext import html_to_plain
from pd_shift.stats import clean_note, collect_notes


def test_html_to_plain_paragraphs_and_breaks():
    html = "<p>Thread spike on db1</p><p>Restarted pool</p><div>Line one<br/>Line two</div>"
    assert html_to_plain(html) == "Thread spike on db1\nRestarted pool\nLine one\nLine two"


def test_html_to_plain_decodes_entities():
    assert html_to_plain("<p>CPU &gt; 90%&nbsp;for 5m</p>") == "CPU > 90% for 5m"


def test_clean_note_strips_html_and_servicenow_url():
    note = (
        "<p>Checked threads.</p>"
        '<p>See <a href="https://percona.service-now.com/incident.do?sys_id=abc">SN</a></p>'
    )
    assert clean_note(note) == "Checked threads.\nSee SN"


def test_print_note_line_with_rich_like_brackets():
    from io import StringIO

    from rich.console import Console

    from pd_shift.stats import _print_note_line

    out = Console(file=StringIO(), width=120, force_terminal=True)
    _print_note_line(out, "query [code]SELECT 1[/code]", first=True)
    assert "[/code]" in out.file.getvalue()

    notes = collect_notes(
        [
            {"content": "<p>Same note</p>"},
            {"content": "<p>Same note</p>"},
            {"content": "<p>https://percona.service-now.com/foo</p>"},
        ]
    )
    assert notes == ["Same note"]
