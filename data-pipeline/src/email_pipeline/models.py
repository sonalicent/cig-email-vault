"""Shared dataclasses passed between pipeline stages.

Kept in one module so parser, splitter, formatter, and handler can share types without
importing each other (avoids circular imports).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Attachment:
    """A real (non-inline) attachment part extracted from a MIME message."""

    filename: str
    content_type: str
    data: bytes


@dataclass
class ParsedEmail:
    """The top-level MIME message: envelope headers, best body, and attachments."""

    message_id: str | None
    from_: str
    to: str
    cc: str
    subject: str
    date: str
    body: str
    body_is_markdown: bool
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class EmailUnit:
    """One individual email extracted from a (possibly forwarded) message body.

    ``order == 0`` is the wrapper unit that maps to the real MIME envelope; later units
    are quoted emails whose headers were parsed from the body text.
    """

    order: int
    is_wrapper: bool
    headers: dict[str, str]
    body: str
    is_external: bool
    message_id: str | None = None

    @property
    def from_(self) -> str:
        return self.headers.get("from", "")

    @property
    def to(self) -> str:
        return self.headers.get("to", "")

    @property
    def cc(self) -> str:
        return self.headers.get("cc", "")

    @property
    def subject(self) -> str:
        return self.headers.get("subject", "")

    @property
    def date(self) -> str:
        return self.headers.get("date", "")
