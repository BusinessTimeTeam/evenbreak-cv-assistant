"""Tests for CV document text extraction.

These build real PDF and Word documents in memory (no fixtures on disk) so the
extraction code is exercised against genuine file bytes.
"""

import io

import pytest
from docx import Document
from reportlab.pdfgen import canvas

from extraction import extract_text


def _make_docx(*paragraphs: str) -> bytes:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_pdf(*lines: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.save()
    return buf.getvalue()


def test_extracts_text_from_docx():
    data = _make_docx("Jane Doe", "Senior Engineer at Acme")
    text = extract_text("cv.docx", data)
    assert "Jane Doe" in text
    assert "Senior Engineer at Acme" in text


def test_extracts_text_from_pdf():
    data = _make_pdf("John Smith", "Product Manager")
    text = extract_text("resume.pdf", data)
    assert "John Smith" in text
    assert "Product Manager" in text


def test_extension_match_is_case_insensitive():
    data = _make_docx("Hello")
    assert "Hello" in extract_text("CV.DOCX", data)


def test_unsupported_extension_raises_valueerror():
    with pytest.raises(ValueError) as exc:
        extract_text("cv.txt", b"plain text")
    assert "txt" in str(exc.value).lower()


def test_unreadable_pdf_raises_valueerror():
    with pytest.raises(ValueError):
        extract_text("broken.pdf", b"not really a pdf")
