"""Render email units and attachments to Markdown + JSON, and build output S3 keys.

Output layout (D12, D23) — keys are prefixed with a thread id + timestamp so every email
of a thread groups together within a date partition and sorts chronologically:
- Emails      → ``emails-extracted/content/{yyyy-mm-dd}/{thread8}__{timestamp}__{subject-slug}__{hash8}.md`` (+ ``.json``)
- Attachments → ``emails-extracted/attachments/{yyyy-mm-dd}/{thread8}__{timestamp}__{eml-stem}__{attachment-slug}.md``

The thread id is a hash of the reply/forward-normalized subject so it is stable across a
thread and works for both the wrapper unit and quoted units (which carry no Message-ID /
References headers). ``timestamp`` is the email's send time in sortable ``YYYYMMDDThhmmssZ``
UTC form.
"""

from __future__ import annotations

import hashlib
import re
from datetime import timezone
from email.utils import parsedate_to_datetime

from .models import Attachment, EmailUnit

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")
# Leading Re:/Fw:/Fwd: (and common non-English variants) reply/forward markers.
_REPLY_PREFIX_RE = re.compile(r"^\s*(re|fw|fwd|aw|wg|sv|vs)\s*(\[\d+\])?\s*:\s*", re.IGNORECASE)
_FALLBACK_TIMESTAMP = "00000000T000000Z"
_SCHEMA_VERSION = 1


def slugify(text: str, max_length: int = 60) -> str:
    """Return a lowercase, URL-safe slug (Outlook EntryIDs must not reach keys, D18)."""
    slug = _SLUG_STRIP_RE.sub("-", (text or "").lower()).strip("-")
    if not slug:
        slug = "untitled"
    return slug[:max_length].strip("-") or "untitled"


def normalize_subject(subject: str) -> str:
    """Strip leading reply/forward prefixes and normalize whitespace/case for threading."""
    text = subject or ""
    prev = None
    while prev != text:
        prev = text
        text = _REPLY_PREFIX_RE.sub("", text, count=1)
    return " ".join(text.split()).lower()


def thread_id(subject: str) -> str:
    """Return a stable 8-char thread id derived from the normalized subject."""
    normalized = normalize_subject(subject) or "no-subject"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]


def format_timestamp(date: str) -> str:
    """Parse an RFC 5322 date into sortable ``YYYYMMDDThhmmssZ`` UTC, or a zero fallback."""
    if date:
        try:
            parsed = parsedate_to_datetime(date)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc)
            return parsed.strftime("%Y%m%dT%H%M%SZ")
    return _FALLBACK_TIMESTAMP


def content_key(prefix: str, date: str, thread: str, timestamp: str, subject: str, hash8: str) -> str:
    """Build the S3 key for a per-email Markdown output (thread + timestamp prefixed)."""
    return f"{prefix}{date}/{thread}__{timestamp}__{slugify(subject)}__{hash8}.md"


def attachment_key(
    prefix: str, date: str, thread: str, timestamp: str, eml_stem: str, filename: str
) -> str:
    """Build the S3 key for a per-attachment Markdown output (thread + timestamp prefixed)."""
    return f"{prefix}{date}/{thread}__{timestamp}__{slugify(eml_stem, 40)}__{slugify(filename, 60)}.md"


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
