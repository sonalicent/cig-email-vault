"""Excel (.xlsx) extraction via openpyxl.

Each sheet becomes a Markdown table; the same data is also returned as structured JSON
(list of sheets → list of row arrays) for downstream tabular consumption (D12, D22).
"""

from __future__ import annotations

import io

import openpyxl

from ._util import ExtractionResult, rows_to_markdown_table


def extract(data: bytes) -> ExtractionResult:
    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        markdown_parts: list[str] = []
        json_sheets: list[dict] = []

        for sheet in workbook.worksheets:
            rows = [
                [_cell(value) for value in row]
                for row in sheet.iter_rows(values_only=True)
            ]
            rows = _trim_empty_rows(rows)
            if not rows:
                continue

            markdown_parts.append(f"## {sheet.title}")
            table = rows_to_markdown_table(rows)
            if table:
                markdown_parts.append(table)

            json_sheets.append({"sheet": sheet.title, "rows": rows})

        return ExtractionResult(
            markdown="\n\n".join(markdown_parts).strip(),
            json_data={"sheets": json_sheets},
        )
    finally:
        workbook.close()


def _cell(value: object) -> object:
    if value is None:
        return ""
    return value


def _trim_empty_rows(rows: list[list[object]]) -> list[list[object]]:
    return [row for row in rows if any(str(cell).strip() for cell in row)]
