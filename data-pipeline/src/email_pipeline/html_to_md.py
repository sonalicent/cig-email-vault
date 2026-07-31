"""Convert an HTML email body to Markdown/plain text.

Used for HTML-only Outlook emails (no ``text/plain`` part) so they still yield real
content rather than empty output (D10, D24).
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from markdownify import markdownify

_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def html_to_markdown(html: str) -> str:
    """Return a Markdown/text rendering of an HTML email body.

    Scripts, styles, and the document head are removed first; embedded images are
    dropped (they are signature/logo noise, handled separately as inline parts).
    """
    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head", "meta", "title"]):
        tag.decompose()

    markdown = markdownify(str(soup), heading_style="ATX", strip=["img"])
    markdown = _MULTI_BLANK_RE.sub("\n\n", markdown)
    return markdown.strip()
