from email_pipeline.formatter import (
    attachment_key,
    content_key,
    email_metadata,
    format_date,
    format_timestamp,
    normalize_subject,
    render_attachment_markdown,
    render_email_markdown,
    slugify,
    thread_id,
)
from email_pipeline.models import Attachment, EmailUnit


def test_slugify_makes_url_safe_slug():
    assert slugify("Fw: Project Jupiter / Gas!") == "fw-project-jupiter-gas"
    assert slugify("") == "untitled"
    assert slugify("!!!") == "untitled"


def test_slugify_truncates():
    assert len(slugify("a" * 200, max_length=20)) <= 20


def test_content_key_layout():
    key = content_key(
        "emails-extracted/content/", "2026-07-27", "deadbeef", "20260727T134500Z", "Re: Timeline", "abcd1234"
    )
    assert key == "emails-extracted/content/2026-07-27/deadbeef__20260727T134500Z__re-timeline__abcd1234.md"


def test_attachment_key_layout():
    key = attachment_key(
        "emails-extracted/attachments/", "2026-07-27", "deadbeef", "20260727T134500Z", "ENTRYID==", "Report Final.pdf"
    )
    assert key.startswith("emails-extracted/attachments/2026-07-27/deadbeef__20260727T134500Z__")
    assert key.endswith("__report-final-pdf.md")


def test_normalize_subject_strips_reply_forward_prefixes():
    assert normalize_subject("Re: Fw: Project Jupiter") == "project jupiter"
    assert normalize_subject("FWD:  Timeline  ") == "timeline"
    assert normalize_subject("Plain Subject") == "plain subject"


def test_thread_id_is_stable_across_reply_forward_variants():
    root = thread_id("Project Jupiter")
    assert thread_id("Re: Project Jupiter") == root
    assert thread_id("Fw: RE: Project Jupiter") == root
    assert thread_id("Different Subject") != root
    assert len(root) == 8


def test_format_timestamp_parses_and_normalizes_to_utc():
    assert format_timestamp("Mon, 27 Jul 2026 13:45:00 +0000") == "20260727T134500Z"
    # Offset is converted to UTC.
    assert format_timestamp("Mon, 27 Jul 2026 06:45:00 -0700") == "20260727T134500Z"
    assert format_timestamp("") == "00000000T000000Z"
    assert format_timestamp("not a date") == "00000000T000000Z"


def test_format_date_parses_and_normalizes_to_utc():
    assert format_date("Mon, 27 Jul 2026 13:45:00 +0000") == "2026-07-27"
    # A positive offset can roll the UTC date back a day.
    assert format_date("Mon, 27 Jul 2026 01:00:00 +0200") == "2026-07-26"
    # A negative offset can roll the UTC date forward a day.
    assert format_date("Mon, 27 Jul 2026 23:00:00 -0300") == "2026-07-28"


def test_format_date_returns_none_for_unusable_input():
    assert format_date("") is None
    assert format_date("not a date") is None


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
