"""Parse a raw `.eml` byte stream into a :class:`ParsedEmail`.

Uses the Python standard-library ``email`` package with ``policy.default`` so
quoted-printable / base64 transfer encodings and iso-8859-1/utf-8 charsets are decoded
transparently (D20). Body selection prefers a non-empty ``text/plain`` part and falls
back to converting ``text/html`` to Markdown (D10). Only parts explicitly marked
``Content-Disposition: attachment`` are treated as attachments; inline signature/logo
images are ignored (D19).
"""

from __future__ import annotations

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

from .html_to_md import html_to_markdown
from .models import Attachment, ParsedEmail


def parse_eml(raw: bytes) -> ParsedEmail:
    """Parse raw `.eml` bytes into a :class:`ParsedEmail`."""
    msg: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw)

    body, is_markdown = _extract_body(msg)
    attachments = _extract_attachments(msg)

    return ParsedEmail(
        message_id=_clean_header(msg["Message-ID"]),
        from_=_clean_header(msg["From"]),
        to=_clean_header(msg["To"]),
        cc=_clean_header(msg["Cc"]),
        subject=_clean_header(msg["Subject"]),
        date=_clean_header(msg["Date"]),
        body=body,
        body_is_markdown=is_markdown,
        attachments=attachments,
    )


def _clean_header(value: object) -> str | None:
    if value is None:
        return None
    # Header objects fold/unfold; collapse internal whitespace introduced by folding.
    text = " ".join(str(value).split())
    return text or None


def _extract_body(msg: EmailMessage) -> tuple[str, bool]:
    """Return ``(body, is_markdown)`` preferring plain text, else HTML→Markdown."""
    plain = msg.get_body(preferencelist=("plain",))
    if plain is not None:
        text = _content_as_str(plain)
        if text.strip():
            return text, False

    html_part = msg.get_body(preferencelist=("html",))
    if html_part is not None:
        html = _content_as_str(html_part)
        if html.strip():
            return html_to_markdown(html), True

    return "", False


def _extract_attachments(msg: EmailMessage) -> list[Attachment]:
    attachments: list[Attachment] = []
    for part in msg.iter_attachments():
        # Only real attachments; skip inline (Content-ID) images / signature logos (D19).
        if (part.get_content_disposition() or "").lower() != "attachment":
            continue
        filename = part.get_filename()
        if not filename:
            continue
        data = part.get_content()
        if isinstance(data, str):
            data = data.encode("utf-8", "replace")
        attachments.append(
            Attachment(
                filename=filename,
                content_type=part.get_content_type(),
                data=data,
            )
        )
    return attachments


def _content_as_str(part: EmailMessage) -> str:
    content = part.get_content()
    if isinstance(content, bytes):
        return content.decode(part.get_content_charset() or "utf-8", "replace")
    return content
