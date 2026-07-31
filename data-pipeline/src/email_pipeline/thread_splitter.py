"""Split an email body into individual (quoted) emails.

Outlook forwards separate quoted emails with a divider line of underscores followed by a
``From:/Sent:/To:/Subject:`` header block. A direct email has no such boundary and yields
exactly one unit. Forwards yield the wrapper (dropped when empty) plus each quoted email
(D9, D15, D16).

``EXTERNAL EMAIL`` banners are detected and preserved as an external-origin marker.
"""

from __future__ import annotations

import re

from .models import EmailUnit

# A line consisting only of underscores (Outlook divider), 5+ to avoid false positives.
_DIVIDER_RE = re.compile(r"^\s*_{5,}\s*$")

# A quoted header line: "From: ...", "Sent: ...", etc.
_HEADER_RE = re.compile(
    r"^\s*(From|Sent|Date|To|Cc|Subject|Importance)\s*:\s?(.*)$",
    re.IGNORECASE,
)

_FROM_RE = re.compile(r"^\s*From\s*:\s?.+$", re.IGNORECASE)
_SENT_OR_DATE_RE = re.compile(r"^\s*(Sent|Date)\s*:\s?.+$", re.IGNORECASE)

_EXTERNAL_RE = re.compile(r"external email", re.IGNORECASE)

# How many lines after a "From:" line we allow before requiring a "Sent:"/"Date:" line.
_HEADER_LOOKAHEAD = 5


def split_thread(body: str) -> list[EmailUnit]:
    """Split ``body`` into ordered :class:`EmailUnit` objects.

    Unit 0 is the wrapper (envelope-owned) text; later units are quoted emails with
    headers parsed from the text. Units whose body is empty are dropped.
    """
    lines = body.splitlines()
    boundaries = _find_boundaries(lines)

    units: list[EmailUnit] = []

    # Wrapper segment: everything before the first quoted header block.
    wrapper_end = boundaries[0] if boundaries else len(lines)
    wrapper_lines = lines[:wrapper_end]
    wrapper_body = _strip_segment("\n".join(wrapper_lines))
    if wrapper_body:
        units.append(
            EmailUnit(
                order=0,
                is_wrapper=True,
                headers={},
                body=wrapper_body,
                is_external=bool(_EXTERNAL_RE.search(wrapper_body)),
            )
        )

    # Quoted segments: each begins at a "From:" header block.
    for idx, start in enumerate(boundaries):
        end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(lines)
        segment_lines = lines[start:end]
        headers, seg_body = _parse_header_block(segment_lines)
        seg_body = _strip_segment(seg_body)
        # Drop header-only blocks: a forward-hop header with no body carries no
        # content (attachments are owned by the wrapper, not quoted units), and
        # nested Outlook forwards stack one such block per hop.
        if not seg_body:
            continue
        units.append(
            EmailUnit(
                order=len(units),
                is_wrapper=False,
                headers=headers,
                body=seg_body,
                is_external=bool(_EXTERNAL_RE.search("\n".join(segment_lines))),
            )
        )

    # Re-number so `order` is contiguous after any drops.
    for position, unit in enumerate(units):
        unit.order = position
        unit.is_wrapper = position == 0 and unit.is_wrapper
    return units


def _find_boundaries(lines: list[str]) -> list[int]:
    """Return indices of lines that start a quoted email header block."""
    boundaries: list[int] = []
    for i, line in enumerate(lines):
        if not _FROM_RE.match(line):
            continue
        window = lines[i + 1 : i + 1 + _HEADER_LOOKAHEAD]
        if any(_SENT_OR_DATE_RE.match(w) for w in window):
            boundaries.append(i)
    return boundaries


def _parse_header_block(segment_lines: list[str]) -> tuple[dict[str, str], str]:
    """Parse leading header lines of a quoted segment; return ``(headers, body)``."""
    headers: dict[str, str] = {}
    current: str | None = None
    body_start = len(segment_lines)

    for i, line in enumerate(segment_lines):
        if _DIVIDER_RE.match(line):
            continue
        match = _HEADER_RE.match(line)
        if match:
            key = match.group(1).lower()
            if key == "sent":  # normalize Outlook "Sent:" to "date"
                key = "date"
            headers[key] = match.group(2).strip()
            current = key
            continue
        if line.strip() == "":
            body_start = i + 1
            break
        if current is not None:
            # Continuation of a wrapped header value (e.g. long To: list).
            headers[current] = f"{headers[current]} {line.strip()}".strip()
            continue
        body_start = i
        break

    body = "\n".join(segment_lines[body_start:])
    return headers, _strip_segment(body)


def _strip_segment(text: str) -> str:
    """Trim surrounding whitespace and leftover divider lines from a segment."""
    lines = [ln for ln in text.splitlines()]
    while lines and (_DIVIDER_RE.match(lines[0]) or not lines[0].strip()):
        lines.pop(0)
    while lines and (_DIVIDER_RE.match(lines[-1]) or not lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip()
