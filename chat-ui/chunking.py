"""Load knowledge-base documents and split them into retrievable chunks.

Chunks follow the document's own structure where possible: markdown sections
first, then paragraphs within a section, and only as a last resort a sliding
window over words (with overlap) for a paragraph too long to keep whole.

This module is copied into the Docker ``index`` stage on its own, so it must
not import other app modules (e.g. extraction.py).
"""

import fnmatch
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = (".md", ".txt", ".pdf")

# Optional file at the data-sources root listing paths (glob patterns, relative
# to the root) to leave out of the index, e.g. a course's table of contents.
IGNORE_FILE = ".indexignore"

_HEADING = re.compile(r"^#{1,6} ", re.MULTILINE)
_BLANK_LINES = re.compile(r"\n\s*\n")
# Dot leaders ("Introduction ........ 23") mark contents pages and lists of
# figures/tables: they match many topics but carry no guidance.
_DOT_LEADER = re.compile(r"\.{5,}")
_CONTENTS_MIN_LEADERS = 5


@dataclass(frozen=True)
class Chunk:
    source: str
    text: str


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def _read_pdf(path: Path) -> str:
    try:
        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except (PyPdfError, OSError, ValueError) as exc:
        logger.warning("Skipping %s: could not read PDF (%s)", path, exc)
        return ""


def _ignore_patterns(root: Path) -> list[str]:
    path = root / IGNORE_FILE
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def load_documents(root: Path) -> list[tuple[str, str]]:
    """Return (relative POSIX path, text) for every indexable file under root."""
    ignored = _ignore_patterns(root)
    documents = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        source = path.relative_to(root).as_posix()
        if source == IGNORE_FILE:
            continue
        if any(fnmatch.fnmatch(source, pattern) for pattern in ignored):
            logger.info("Skipping %s: listed in %s", source, IGNORE_FILE)
            continue
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            logger.warning("Skipping %s: unsupported file type", source)
            continue
        text = _read_pdf(path) if suffix == ".pdf" else path.read_text(errors="replace")
        if not text.strip():
            logger.warning("Skipping %s: no extractable text", source)
            continue
        documents.append((source, text))
    if not documents:
        raise ValueError(f"no indexable documents under {root}")
    return documents


def _sections(text: str) -> list[str]:
    starts = [m.start() for m in _HEADING.finditer(text)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    bounds = starts + [len(text)]
    return [text[a:b].strip() for a, b in zip(bounds, bounds[1:])]


def _windows(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    words = text.split()
    max_chars, overlap_chars = max_tokens * 4, overlap_tokens * 4
    windows, start = [], 0
    while start < len(words):
        end, length = start, 0
        while end < len(words) and length + len(words[end]) + 1 <= max_chars:
            length += len(words[end]) + 1
            end += 1
        end = max(end, start + 1)  # a single over-long word still makes progress
        windows.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        # Step back far enough to carry ~overlap_tokens into the next window.
        back, carried = end, 0
        while back > start + 1 and carried + len(words[back - 1]) + 1 <= overlap_chars:
            back -= 1
            carried += len(words[back]) + 1
        start = back
    return windows


def _is_contents_listing(text: str) -> bool:
    return len(_DOT_LEADER.findall(text)) >= _CONTENTS_MIN_LEADERS


def _merge_small(chunks: list[Chunk], min_tokens: int, max_tokens: int) -> list[Chunk]:
    """Fold chunks under min_tokens into a neighbour while the result still fits."""
    merged: list[Chunk] = []
    for chunk in chunks:
        if merged:
            previous = merged[-1]
            combined = f"{previous.text}\n\n{chunk.text}"
            small = estimate_tokens(previous.text) < min_tokens or estimate_tokens(chunk.text) < min_tokens
            if small and estimate_tokens(combined) <= max_tokens:
                merged[-1] = Chunk(previous.source, combined)
                continue
        merged.append(chunk)
    return merged


def chunk_document(
    source: str,
    text: str,
    *,
    max_tokens: int = 500,
    overlap_tokens: int = 50,
    min_tokens: int = 100,
) -> list[Chunk]:
    chunks = []
    for section in _sections(text):
        if not section:
            continue
        # Pieces small enough to stand alone: paragraphs, or windows of an
        # oversized paragraph.
        pieces = []
        for paragraph in _BLANK_LINES.split(section):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if estimate_tokens(paragraph) <= max_tokens:
                pieces.append(paragraph)
            else:
                pieces.extend(_windows(paragraph, max_tokens, overlap_tokens))
        pieces = [piece for piece in pieces if not _is_contents_listing(piece)]
        # Greedily merge adjacent pieces within the section up to max_tokens.
        current = ""
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if estimate_tokens(candidate) <= max_tokens:
                current = candidate
            else:
                chunks.append(Chunk(source, current))
                current = piece
        if current:
            chunks.append(Chunk(source, current))
    return _merge_small(chunks, min_tokens, max_tokens)
