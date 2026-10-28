"""Text extraction for uploaded CV documents.

The chat model only accepts text, so uploaded CVs (PDF, Word, OpenDocument, RTF
or plain text) are converted to plain text here before being handed to the
model as conversation context.
"""

import io
import xml.etree.ElementTree as ET
import zipfile

import pdfplumber
from docx import Document
from docx.oxml.ns import qn
from striprtf.striprtf import rtf_to_text

from pdf_text import page_text

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".odt", ".rtf", ".txt", ".md")

# Table cells are joined with this when every cell in a row is a single line
# (e.g. "Engineer, Acme | 2019 - 2021"); a row of multi-line cells is a layout
# table, so each cell is kept together instead.
CELL_SEPARATOR = " | "


def _table_rows(rows) -> list[str]:
    """Flatten table rows, given as lists of cells, each a list of lines."""
    lines = []
    for cells in rows:
        cells = [c for c in cells if c]
        if all(len(c) == 1 for c in cells):
            if cells:
                lines.append(CELL_SEPARATOR.join(c[0] for c in cells))
        else:
            for c in cells:
                lines.extend(c)
    return lines


# --- PDF -------------------------------------------------------------------

def _extract_pdf(data: bytes) -> str:
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [page_text(page) for page in pdf.pages]
            uris = [link.get("uri") for page in pdf.pages for link in page.hyperlinks]
    except Exception as exc:  # pdfminer raises a variety of types on bad input
        raise ValueError(f"Could not read PDF: {exc}") from exc
    text = "\n".join(pages)
    # A link's visible text ("LinkedIn") often hides its target, so list any
    # target the text doesn't already show.
    links = []
    for uri in uris:
        target = (uri or "").removeprefix("mailto:")
        if target and target not in text and target not in links:
            links.append(target)
    if links:
        text += "\n\nLinks:\n" + "\n".join(links)
    return text


# --- Word ------------------------------------------------------------------

_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"


def _docx_runs(el, out: list[str], boxes: list) -> None:
    """Collect a paragraph's text into out and its text boxes into boxes."""
    for child in el:
        tag = child.tag
        if tag == qn("w:t"):
            out.append(child.text or "")
        elif tag == qn("w:tab"):
            out.append("\t")
        elif tag in (qn("w:br"), qn("w:cr")):
            out.append("\n")
        elif tag == qn("w:txbxContent"):
            boxes.append(child)
        elif tag != _MC_FALLBACK:  # the fallback repeats the text box's text
            _docx_runs(child, out, boxes)


def _docx_blocks(el) -> list[str]:
    """Return the lines of a body, cell, header, footer or text box."""
    lines = []
    for child in el:
        if child.tag == qn("w:p"):
            out, boxes = [], []
            _docx_runs(child, out, boxes)
            text = "".join(out)
            if text.strip():
                lines.append(text)
            for box in boxes:
                lines.extend(_docx_blocks(box))
        elif child.tag == qn("w:tbl"):
            rows = [
                [_docx_blocks(tc) for tc in tr.iter(qn("w:tc")) if tc.getparent() is tr]
                for tr in child.iter(qn("w:tr"))
                if tr.getparent() is child
            ]
            lines.extend(_table_rows(rows))
        elif child.tag in (qn("w:sdt"), qn("w:sdtContent"), qn("w:customXml")):
            lines.extend(_docx_blocks(child))
    return lines


def _extract_docx(data: bytes) -> str:
    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises a variety of types on bad input
        raise ValueError(f"Could not read Word document: {exc}") from exc
    headers, footers, seen = [], [], set()
    for section in doc.sections:
        for part, dest in (
            (section.first_page_header, headers), (section.header, headers),
            (section.even_page_header, headers), (section.first_page_footer, footers),
            (section.footer, footers), (section.even_page_footer, footers),
        ):
            # A linked header has no content of its own; shared ones appear once.
            if part.is_linked_to_previous or id(part.part) in seen:
                continue
            seen.add(id(part.part))
            dest.extend(_docx_blocks(part._element))
    return "\n".join(headers + _docx_blocks(doc.element.body) + footers)


# --- OpenDocument ----------------------------------------------------------

_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"


def _odt_inline(el, out: list[str]) -> None:
    out.append(el.text or "")
    for child in el:
        if child.tag == f"{_TEXT}s":
            out.append(" " * int(child.get(f"{_TEXT}c", "1")))
        elif child.tag == f"{_TEXT}tab":
            out.append("\t")
        elif child.tag == f"{_TEXT}line-break":
            out.append("\n")
        elif child.tag != f"{_TEXT}note":
            _odt_inline(child, out)
        out.append(child.tail or "")


def _odt_blocks(el) -> list[str]:
    lines = []
    for child in el:
        if child.tag in (f"{_TEXT}p", f"{_TEXT}h"):
            out = []
            _odt_inline(child, out)
            text = "".join(out)
            if text.strip():
                lines.append(text)
        elif child.tag == f"{_TABLE}table":
            rows = [
                [_odt_blocks(cell) for cell in row.iter(f"{_TABLE}table-cell")]
                for row in child.iter(f"{_TABLE}table-row")
            ]
            lines.extend(_table_rows(rows))
        else:  # lists, sections, frames and the like hold further blocks
            lines.extend(_odt_blocks(child))
    return lines


def _extract_odt(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            content = ET.fromstring(z.read("content.xml"))
            styles = ET.fromstring(z.read("styles.xml")) if "styles.xml" in z.namelist() else None
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError(f"Could not read OpenDocument file: {exc}") from exc
    headers, footers = [], []
    if styles is not None:
        for page in styles.iter(f"{_STYLE}master-page"):
            for part in page.iter(f"{_STYLE}header"):
                headers.extend(_odt_blocks(part))
            for part in page.iter(f"{_STYLE}footer"):
                footers.extend(_odt_blocks(part))
    body = [line for text in content.iter(f"{_OFFICE}text") for line in _odt_blocks(text)]
    return "\n".join(headers + body + footers)


# --- RTF and plain text ----------------------------------------------------

def _extract_rtf(data: bytes) -> str:
    try:
        return rtf_to_text(data.decode("latin-1"), errors="ignore")
    except Exception as exc:  # striprtf raises a variety of types on bad input
        raise ValueError(f"Could not read RTF document: {exc}") from exc


def _extract_plain(data: bytes) -> str:
    # UTF-8 first (dropping any byte-order mark), then the Windows encoding
    # older editors such as Notepad save in.
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("Could not read text file: unrecognised character encoding")


_EXTRACTORS = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".odt": _extract_odt,
    ".rtf": _extract_rtf,
    ".txt": _extract_plain,
    ".md": _extract_plain,
}


def extract_text(filename: str, data: bytes) -> str:
    """Extract plain text from an uploaded CV.

    Dispatches on the file extension. Raises ValueError for unsupported types
    or unreadable files.
    """
    lower = filename.lower()
    for extension, extractor in _EXTRACTORS.items():
        if lower.endswith(extension):
            return extractor(data)
    raise ValueError(
        f"Unsupported file type for '{filename}'. "
        f"Supported types: {', '.join(SUPPORTED_EXTENSIONS)}."
    )
