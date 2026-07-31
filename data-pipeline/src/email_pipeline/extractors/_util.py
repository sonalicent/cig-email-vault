"""Shared helpers for attachment extractors."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractionResult:
    """Extracted attachment content: Markdown plus optional structured JSON data."""

    markdown: str
    json_data: object | None = field(default=None)


def rows_to_markdown_table(rows: list[list[object]]) -> str:
    """Render a list of rows (first row = header) as a GitHub-flavoured Markdown table."""
    cleaned = [["" if c is None else str(c) for c in row] for row in rows if row]
    if not cleaned:
        return ""

    width = max(len(r) for r in cleaned)
    cleaned = [r + [""] * (width - len(r)) for r in cleaned]

    header = cleaned[0]
    body = cleaned[1:] if len(cleaned) > 1 else []

    def _row(cells: list[str]) -> str:
        return "| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |"

    lines = [_row(header), "| " + " | ".join(["---"] * width) + " |"]
    lines.extend(_row(r) for r in body)
    return "\n".join(lines)
