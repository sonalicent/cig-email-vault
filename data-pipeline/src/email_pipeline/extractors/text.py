"""Plain-text / CSV attachment extraction (decode only)."""

from __future__ import annotations

from ._util import ExtractionResult

_ENCODINGS = ("utf-8", "utf-16", "iso-8859-1")


def extract(data: bytes) -> ExtractionResult:
    text = _decode(data)
    return ExtractionResult(markdown=text.strip())


def _decode(data: bytes) -> str:
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")
