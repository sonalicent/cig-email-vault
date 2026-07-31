from conftest import make_docx, make_pdf, make_pptx, make_txt, make_xlsx

from email_pipeline.extractors import extract


def test_text_extractor():
    result = extract("notes.txt", make_txt("plain content here"))
    assert result is not None
    assert "plain content here" in result.markdown


def test_xlsx_extractor_emits_markdown_table_and_json():
    result = extract("data.xlsx", make_xlsx([["Name", "Value"], ["Alpha", 1]]))
    assert result is not None
    assert "| Name | Value |" in result.markdown
    assert "Alpha" in result.markdown
    assert result.json_data is not None
    assert result.json_data["sheets"][0]["sheet"] == "Data"


def test_docx_extractor():
    result = extract("doc.docx", make_docx(["First paragraph.", "Second paragraph."]))
    assert result is not None
    assert "First paragraph." in result.markdown
    assert "Second paragraph." in result.markdown


def test_pptx_extractor():
    result = extract("deck.pptx", make_pptx(["Slide one text", "Slide two text"]))
    assert result is not None
    assert "Slide one text" in result.markdown
    assert "## Slide 1" in result.markdown


def test_pdf_extractor():
    result = extract("file.pdf", make_pdf("Hello PDF"))
    assert result is not None
    assert "Hello PDF" in result.markdown


def test_unsupported_type_returns_none():
    assert extract("archive.zip", b"PK\x03\x04") is None


def test_corrupt_file_degrades_gracefully():
    # Not a valid xlsx; extractor should catch and return None rather than raise.
    assert extract("bad.xlsx", b"not really a spreadsheet") is None
