from email_pipeline.formatter import (
    attachment_key,
    content_key,
    email_metadata,
    render_attachment_markdown,
    render_email_markdown,
    slugify,
)
from email_pipeline.models import Attachment, EmailUnit


def test_slugify_makes_url_safe_slug():
    assert slugify("Fw: Project Jupiter / Gas!") == "fw-project-jupiter-gas"
    assert slugify("") == "untitled"
    assert slugify("!!!") == "untitled"


def test_slugify_truncates():
    assert len(slugify("a" * 200, max_length=20)) <= 20


def test_content_key_layout():
    key = content_key("emails-extracted/content/", "2026-07-27", "Re: Timeline", "abcd1234")
    assert key == "emails-extracted/content/2026-07-27/re-timeline__abcd1234.md"


def test_attachment_key_layout():
    key = attachment_key("emails-extracted/attachments/", "2026-07-27", "ENTRYID==", "Report Final.pdf")
    assert key.startswith("emails-extracted/attachments/2026-07-27/")
    assert key.endswith("__report-final-pdf.md")


def test_render_email_markdown_includes_header_block_and_external_marker():
    unit = EmailUnit(
        order=0,
        is_wrapper=True,
        headers={"from": "Alice <a@x.com>", "to": "Bob <b@x.com>", "subject": "Hi", "date": "today"},
        body="Body text here.",
        is_external=True,
        message_id="<m1>",
    )
    md = render_email_markdown(unit)
    assert "# Hi" in md
    assert "**From:** Alice <a@x.com>" in md
    assert "EXTERNAL EMAIL" in md
    assert "Body text here." in md


def test_email_metadata_shape():
    unit = EmailUnit(0, True, {"subject": "S", "from": "F"}, "body", False, "<m>")
    meta = email_metadata(unit, "id-1", "emails/x.eml", "out.md", [{"filename": "r.txt"}])
    assert meta["identity"] == "id-1"
    assert meta["subject"] == "S"
    assert meta["attachments"] == [{"filename": "r.txt"}]
    assert meta["schema_version"] == 1


def test_render_attachment_markdown():
    att = Attachment(filename="report.txt", content_type="text/plain", data=b"x")
    md = render_attachment_markdown(att, "emails/2026-07-27/x.eml", "extracted content")
    assert "# Attachment: report.txt" in md
    assert "extracted content" in md
