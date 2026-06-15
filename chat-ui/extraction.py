"""Text extraction for uploaded CV documents.

The managed agent only accepts text, so uploaded CVs (PDF / Word) are converted
to plain text here before being handed to the agent as conversation context.
"""

import io

from docx import Document
from pypdf import PdfReader
from pypdf.errors import PyPdfError

# Map of supported file extension -> human-readable label.
SUPPORTED_EXTENSIONS = (".pdf", ".docx")


def _extract_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except (PyPdfError, OSError, ValueError) as exc:
        raise ValueError(f"Could not read PDF: {exc}") from exc


def _extract_docx(data: bytes) -> str:
    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises a variety of types on bad input
        raise ValueError(f"Could not read Word document: {exc}") from exc
    return "\n".join(p.text for p in doc.paragraphs)


def extract_text(filename: str, data: bytes) -> str:
    """Extract plain text from an uploaded CV.

    Dispatches on the file extension. Raises ValueError for unsupported types
    or unreadable files.
    """
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return _extract_pdf(data)
    if lower.endswith(".docx"):
        return _extract_docx(data)
    raise ValueError(
        f"Unsupported file type for '{filename}'. "
        f"Supported types: {', '.join(SUPPORTED_EXTENSIONS)}."
    )
