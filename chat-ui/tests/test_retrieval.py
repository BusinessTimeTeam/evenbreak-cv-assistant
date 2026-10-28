"""Tests for building, saving, loading and searching the knowledge-base index."""

import asyncio

import numpy as np
import pytest

import retrieval
from chunking import Chunk
from inference import InferenceConfig
from retrieval import EmbeddingModelMismatch, Index, IndexLoadError, source_hash

MODEL = "qwen3-embedding-0.6b"


def _index(**overrides):
    chunks = [Chunk("a", "alpha"), Chunk("b", "beta"), Chunk("c", "gamma")]
    vectors = np.array([[1, 0, 0], [0, 1, 0], [0.9, 0.1, 0]], dtype=np.float32)
    kwargs = {"embedding_model": MODEL, "source_hash": "h1", "built_at": "2026-10-28T00:00:00Z"}
    kwargs.update(overrides)
    return Index(chunks, vectors, **kwargs)


def test_search_returns_most_similar_first():
    results = _index().search([[1, 0, 0]], k=2)

    assert [r.source for r in results] == ["a", "c"]
    assert results[0].score > results[1].score


def test_search_merges_multiple_queries_without_duplicates():
    results = _index().search([[1, 0, 0], [0.95, 0.05, 0]], k=3)

    sources = [r.source for r in results]
    assert sources.count("a") == 1
    assert results[0].source == "a"
    assert results[0].score == pytest.approx(1.0)


def test_search_k_larger_than_index_returns_all():
    assert len(_index().search([[1, 0, 0]], k=10)) == 3


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "kb_index.npz"
    original = _index()

    original.save(path)
    loaded = Index.load(path, expected_embedding_model=MODEL)

    assert len(loaded) == 3
    assert loaded.embedding_model == MODEL
    assert loaded.source_hash == "h1"
    assert loaded.search([[0, 1, 0]], k=3) == original.search([[0, 1, 0]], k=3)


def test_load_rejects_different_embedding_model(tmp_path):
    path = tmp_path / "kb_index.npz"
    _index().save(path)

    with pytest.raises(EmbeddingModelMismatch) as excinfo:
        Index.load(path, expected_embedding_model="bge-m3")

    assert MODEL in str(excinfo.value)
    assert "bge-m3" in str(excinfo.value)


def test_load_missing_file_raises_helpful_error(tmp_path):
    path = tmp_path / "missing.npz"

    with pytest.raises(IndexLoadError) as excinfo:
        Index.load(path, expected_embedding_model=MODEL)

    assert str(path) in str(excinfo.value)
    assert "docker compose build" in str(excinfo.value)


def test_load_corrupt_file_raises_helpful_error(tmp_path):
    path = tmp_path / "kb_index.npz"
    path.write_bytes(b"not an index")

    with pytest.raises(IndexLoadError, match="docker compose build"):
        Index.load(path, expected_embedding_model=MODEL)


def test_load_truncated_index_raises_helpful_error(tmp_path):
    # e.g. an interrupted build: a real .npz cut short is a BadZipFile.
    path = tmp_path / "kb_index.npz"
    _index().save(path)
    path.write_bytes(path.read_bytes()[: path.stat().st_size // 2])

    with pytest.raises(IndexLoadError, match="docker compose build"):
        Index.load(path, expected_embedding_model=MODEL)


def test_load_index_with_incomplete_metadata_raises_helpful_error(tmp_path):
    path = tmp_path / "kb_index.npz"
    np.savez_compressed(
        path,
        vectors=np.eye(2, dtype=np.float32),
        sources=np.array(["a", "b"]),
        texts=np.array(["alpha", "beta"]),
        metadata=np.array('{"embedding_model": "qwen3-embedding-0.6b"}'),
    )

    with pytest.raises(IndexLoadError, match="docker compose build"):
        Index.load(path, expected_embedding_model=MODEL)


def test_failed_save_leaves_the_existing_index_intact(tmp_path, monkeypatch):
    path = tmp_path / "kb_index.npz"
    _index().save(path)

    def broken_savez(fh, **arrays):
        fh.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(retrieval.np, "savez_compressed", broken_savez)
    with pytest.raises(OSError):
        _index(source_hash="h2").save(path)

    assert Index.load(path, expected_embedding_model=MODEL).source_hash == "h1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kb_index.npz"]


def test_build_index_embeds_every_chunk_and_records_metadata(tmp_path, monkeypatch):
    (tmp_path / "one.md").write_text("## One\nReasonable adjustments at interview.")
    (tmp_path / "two.md").write_text("## Two\nDisclosing a disability on a CV.")
    embedded = []

    async def fake_embed(config, texts, **kwargs):
        embedded.extend(texts)
        return [[float(i + 1), 1.0, 0.0] for i in range(len(texts))]

    monkeypatch.setattr(retrieval, "embed", fake_embed)
    config = InferenceConfig(api_key="k")

    index = asyncio.run(retrieval.build_index(tmp_path, config))

    assert len(index) == len(embedded) == 2
    assert index.embedding_model == config.embedding_model
    # The embedded text carries the file name; the stored text does not.
    assert embedded[0].startswith("one.md\n")
    assert index.search([[1.0, 1.0, 0.0]], k=1)[0].text.startswith("## One")


def test_source_hash_changes_when_a_document_changes():
    docs = [("a.md", "one"), ("b.md", "two")]

    assert source_hash(docs) == source_hash(list(reversed(docs)))
    assert source_hash(docs) != source_hash([("a.md", "one"), ("b.md", "changed")])
