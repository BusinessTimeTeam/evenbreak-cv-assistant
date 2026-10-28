"""Tests for loading knowledge-base documents and splitting them into chunks."""

import logging

from reportlab.pdfgen import canvas

import pytest

from chunking import Chunk, chunk_document, estimate_tokens, load_documents


def _pdf(path, text=None):
    c = canvas.Canvas(str(path))
    if text:
        c.drawString(72, 720, text)
    c.showPage()
    c.save()


def test_load_documents_reads_md_txt_and_pdf_with_relative_sources(tmp_path):
    (tmp_path / "a.md").write_text("# A\nMarkdown body")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("Plain text body")
    _pdf(tmp_path / "sub" / "c.pdf", "PDF body text")

    docs = load_documents(tmp_path)

    assert [source for source, _ in docs] == ["a.md", "sub/b.txt", "sub/c.pdf"]
    assert "Markdown body" in docs[0][1]
    assert "PDF body text" in docs[2][1]


def test_load_documents_skips_unsupported_and_empty_files(tmp_path, caplog):
    (tmp_path / "keep.md").write_text("Useful guidance")
    (tmp_path / "notes.docx").write_bytes(b"not indexed")
    (tmp_path / "empty.md").write_text("  \n\n ")
    _pdf(tmp_path / "blank.pdf")

    with caplog.at_level(logging.WARNING):
        docs = load_documents(tmp_path)

    assert [source for source, _ in docs] == ["keep.md"]
    for name in ("notes.docx", "empty.md", "blank.pdf"):
        assert name in caplog.text


def test_load_documents_raises_when_nothing_indexable(tmp_path):
    (tmp_path / "empty.md").write_text("")

    with pytest.raises(ValueError, match=str(tmp_path)):
        load_documents(tmp_path)


def test_load_documents_skips_paths_listed_in_indexignore(tmp_path, caplog):
    (tmp_path / "course").mkdir()
    (tmp_path / "course" / "01-guidance.md").write_text("Useful guidance")
    (tmp_path / "course" / "02-table-of-contents.md").write_text("Contents of every module")
    (tmp_path / "notes.draft.md").write_text("Work in progress")
    (tmp_path / ".indexignore").write_text(
        "# Pages that match many topics but carry no guidance\n"
        "course/02-table-of-contents.md\n"
        "\n"
        "*.draft.md\n"
    )

    with caplog.at_level(logging.WARNING):
        docs = load_documents(tmp_path)

    assert [source for source, _ in docs] == ["course/01-guidance.md"]
    # The ignore file itself is configuration, not an unsupported document.
    assert ".indexignore" not in caplog.text


def test_contents_listings_are_dropped_from_chunks():
    contents = "\n".join(
        f"Chapter {i} Topic number {i} {'.' * 60} {i * 10}" for i in range(1, 13)
    )
    guidance = (
        "Reasonable adjustments are changes an employer makes to remove barriers. "
        "They can include flexible hours, assistive technology or a quiet workspace. " * 6
    )
    text = f"Contents\n{contents}\n\n## Adjustments\n{guidance}"

    chunks = chunk_document("report.pdf", text)

    assert chunks
    assert all("....." not in c.text for c in chunks)
    assert any("flexible hours" in c.text for c in chunks)


def test_short_document_is_one_chunk():
    text = " ".join(["word"] * 100)

    chunks = chunk_document("doc.md", text)

    assert chunks == [Chunk(source="doc.md", text=text)]


def test_chunks_respect_max_tokens():
    paragraphs = [" ".join([f"p{i}w{j}" for j in range(80)]) for i in range(40)]
    text = "\n\n".join(paragraphs)

    chunks = chunk_document("doc.md", text)

    assert len(chunks) > 1
    assert all(estimate_tokens(c.text) <= 500 for c in chunks)


def _body(topic, words=120):
    return " ".join(f"{topic}{i}" for i in range(words))


def test_chunks_split_on_markdown_headings_first():
    # Each section is substantial (~150+ tokens), so they stay separate even
    # though together they would fit in one chunk.
    text = (
        f"## First section\n{_body('adjust')}\n\n"
        f"## Second section\n{_body('disclose')}"
    )

    chunks = chunk_document("doc.md", text)

    assert [c.text.splitlines()[0] for c in chunks] == ["## First section", "## Second section"]


def test_tiny_sections_are_merged_with_their_neighbours():
    # Course exports contain heading-only and near-empty sections; on their own
    # they retrieve badly, so they are folded into the following section.
    text = (
        "# Module title\n\n"
        "## Text: subheading paragraph\n\n"
        f"## Real content\n{_body('content')}\n\n"
        "## Continue: continue"
    )

    chunks = chunk_document("doc.md", text)

    assert len(chunks) == 1
    assert chunks[0].text.startswith("# Module title")
    assert "content0" in chunks[0].text
    assert chunks[0].text.endswith("## Continue: continue")


def test_consecutive_chunks_overlap():
    # One oversized paragraph with no blank lines forces window splitting.
    text = " ".join(f"word{i}" for i in range(1500))

    chunks = chunk_document("doc.md", text)

    assert len(chunks) > 1
    for current, following in zip(chunks, chunks[1:]):
        tail = " ".join(current.text.split()[-5:])
        assert following.text.startswith(tail) or tail in following.text[:400]


def test_no_empty_chunks():
    text = "Intro\n\n\n\n\n\n## Heading\n\n\n\nBody\n\n\n\n"

    chunks = chunk_document("doc.md", text)

    assert chunks
    assert all(c.text.strip() for c in chunks)


def test_every_chunk_records_its_source():
    text = "## One\nA\n\n## Two\nB"

    chunks = chunk_document("guides/x.md", text)

    assert {c.source for c in chunks} == {"guides/x.md"}
