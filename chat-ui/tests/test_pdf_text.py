"""Tests for reading PDF pages as text (shared by CV extraction and the index)."""

import io

import pdfplumber
from reportlab.pdfgen import canvas

from pdf_text import page_text

LINES = ["Table 2: chronic diseases", "Alcoholic liver disease", "Cost to the economy"]


def _rotated_pdf(angle: int) -> bytes:
    """One page whose text is all rotated by angle degrees, like a landscape
    table on a portrait page. Lines are stacked in reading order."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, line in enumerate(LINES):
        c.saveState()
        # Turned anticlockwise (90), text runs up the page and lines stack
        # rightwards; turned clockwise (-90), it runs down and stacks leftwards.
        if angle == 90:
            c.translate(100 + i * 20, 100)
        else:
            c.translate(500 - i * 20, 750)
        c.rotate(angle)
        c.drawString(0, 0, line)
        c.restoreState()
    c.save()
    return buf.getvalue()


def _text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return page_text(pdf.pages[0])


def test_reads_text_turned_anticlockwise():
    assert _text(_rotated_pdf(90)).splitlines() == LINES


def test_reads_text_turned_clockwise():
    assert _text(_rotated_pdf(-90)).splitlines() == LINES


def test_reads_sideways_labels_on_an_upright_page():
    # A table whose column headings are turned on their side.
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 750, "Employment rates of disabled people by country, 2020")
    for i, country in enumerate(["Africa", "Cambodia", "Mongolia"]):
        c.saveState()
        c.translate(100 + i * 20, 600)
        c.rotate(90)
        c.drawString(0, 0, country)
        c.restoreState()
    c.save()
    text = _text(buf.getvalue())
    assert "Employment rates of disabled people by country, 2020" in text
    for country in ("Africa", "Cambodia", "Mongolia"):
        assert country in text
