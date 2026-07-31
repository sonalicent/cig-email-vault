from conftest import read_fixture
from email_pipeline.eml_parser import parse_eml


def test_parses_direct_plain_headers_and_body():
    parsed = parse_eml(read_fixture("direct_plain.eml"))
    assert parsed.from_ == "Alice Example <alice@example.com>"
    assert parsed.subject == "Water demand numbers"
    assert parsed.message_id == "<direct-001@example.com>"
    assert parsed.body_is_markdown is False
    assert "water demand numbers" in parsed.body.lower()
    assert parsed.attachments == []


def test_html_only_body_is_converted_to_markdown():
    parsed = parse_eml(read_fixture("html_only.eml"))
    assert parsed.body_is_markdown is True
    assert "**team**" in parsed.body
    assert parsed.body.strip() != ""


def test_attachment_parts_filtered_to_real_attachments():
    parsed = parse_eml(read_fixture("with_attachment.eml"))
    # Only the real attachment; the inline image001.png must be excluded (D19).
    filenames = [a.filename for a in parsed.attachments]
    assert filenames == ["report.txt"]
    assert b"Line one of report" in parsed.attachments[0].data


def test_body_prefers_plain_over_attachment_text():
    parsed = parse_eml(read_fixture("with_attachment.eml"))
    assert "Please find the report attached." in parsed.body
