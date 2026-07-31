"""Render email units and attachments to Markdown + JSON, and build output S3 keys.

Output layout (D12, D23):
- Emails      → ``emails-extracted/content/{yyyy-mm-dd}/{subject-slug}__{hash8}.md`` (+ ``.json``)
- Attachments → ``emails-extracted/attachments/{yyyy-mm-dd}/{eml-stem}__{attachment-slug}.md``
"""

from __future__ import annotations

import re

from .models import Attachment, EmailUnit

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")
_SCHEMA_VERSION = 1


def slugify(text: str, max_length: int = 60) -> str:
    """Return a lowercase, URL-safe slug (Outlook EntryIDs must not reach keys, D18)."""
    slug = _SLUG_STRIP_RE.sub("-", (text or "").lower()).strip("-")
    if not slug:
        slug = "untitled"
    return slug[:max_length].strip("-") or "untitled"


def content_key(prefix: str, date: str, subject: str, hash8: str) -> str:
    """Build the S3 key for a per-email Markdown output."""
    return f"{prefix}{date}/{slugify(subject)}__{hash8}.md"


def attachment_key(prefix: str, date: str, eml_stem: str, filename: str) -> str:
    """Build the S3 key for a per-attachment Markdown output."""
    return f"{prefix}{date}/{slugify(eml_stem, 40)}__{slugify(filename, 60)}.md"


def render_email_markdown(unit: EmailUnit) -> str:
    """Render one email unit as Markdown with a header block + body."""
    subject = unit.subject or "(no subject)"
    lines = [f"# {subject}", ""]

    if unit.is_external:
        lines.append("> **EXTERNAL EMAIL**")
        lines.append("")

    for label, value in (
        ("From", unit.from_),
        ("To", unit.to),
        ("Cc", unit.cc),
        ("Date", unit.date),
    ):
        if value:
            lines.append(f"**{label}:** {value}  ")

    lines.extend(["", "---", "", unit.body.strip(), ""])
    return "\n".join(lines).strip() + "\n"


def email_metadata(
    unit: EmailUnit,
    identity: str,
    source_key: str,
    output_key: str,
    attachment_refs: list[dict] | None = None,
) -> dict:
    """Build the JSON metadata sidecar for an email unit."""
    return {
        "schema_version": _SCHEMA_VERSION,
        "identity": identity,
        "message_id": unit.message_id,
        "source_key": source_key,
        "output_key": output_key,
        "order": unit.order,
        "is_wrapper": unit.is_wrapper,
        "is_external": unit.is_external,
        "subject": unit.subject,
        "from": unit.from_,
        "to": unit.to,
        "cc": unit.cc,
        "date": unit.date,
        "attachments": attachment_refs or [],
    }


def render_attachment_markdown(attachment: Attachment, source_key: str, markdown: str) -> str:
    """Render an extracted attachment as Markdown."""
    lines = [
        f"# Attachment: {attachment.filename}",
        "",
        f"**Source email:** `{source_key}`  ",
        f"**Content type:** {attachment.content_type}  ",
        "",
        "---",
        "",
        markdown.strip(),
        "",
    ]
    return "\n".join(lines).strip() + "\n"


def attachment_metadata(
    attachment: Attachment,
    source_key: str,
    output_key: str,
    json_data: object | None = None,
) -> dict:
    """Build the JSON sidecar for an attachment (includes tabular data for xlsx, D22)."""
    metadata: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "filename": attachment.filename,
        "content_type": attachment.content_type,
        "source_key": source_key,
        "output_key": output_key,
    }
    if json_data is not None:
        metadata["data"] = json_data
    return metadata
