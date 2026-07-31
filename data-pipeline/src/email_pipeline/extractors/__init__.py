"""Attachment text extraction, dispatched by file extension (D11).

Only real attachments reach here (inline images are filtered in the parser, D19).
Supported: pdf, pptx, xlsx, docx, and plain text/csv. Unknown types return ``None`` and
are skipped + logged by the caller.
"""

from __future__ import annotations

import logging
import os

from . import docx as _docx
from . import pdf as _pdf
from . import pptx as _pptx
from . import text as _text
from . import xlsx as _xlsx
from ._util import ExtractionResult, rows_to_markdown_table

__all__ = ["ExtractionResult", "extract", "rows_to_markdown_table"]

logger = logging.getLogger(__name__)


# Extension → extractor function returning an :class:`ExtractionResult`.
_DISPATCH = {
    ".pdf": _pdf.extract,
    ".pptx": _pptx.extract,
    ".xlsx": _xlsx.extract,
    ".docx": _docx.extract,
    ".txt": _text.extract,
    ".csv": _text.extract,
    ".log": _text.extract,
    ".md": _text.extract,
}


def extract(filename: str, data: bytes) -> ExtractionResult | None:
    """Extract text from an attachment, or ``None`` if the type is unsupported/failed."""
    ext = os.path.splitext(filename)[1].lower()
    extractor = _DISPATCH.get(ext)
    if extractor is None:
        logger.info("Skipping unsupported attachment type: %s", filename)
        return None

    try:
        return extractor(data)
    except Exception:  # noqa: BLE001 - graceful degradation on a bad/corrupt file
        logger.exception("Failed to extract attachment: %s", filename)
        return None
