"""PDF text + table extraction via pdfplumber."""

from __future__ import annotations

import io

import pdfplumber

from ._util import ExtractionResult, rows_to_markdown_table


def extract(data: bytes) -> ExtractionResult:
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            parts.append(f"## Page {page_number}")

            text = page.extract_text() or ""
            if text.strip():
                parts.append(text.strip())

            for table in page.extract_tables() or []:
                markdown_table = rows_to_markdown_table(table)
                if markdown_table:
                    parts.append(markdown_table)

    return ExtractionResult(markdown="\n\n".join(parts).strip())
