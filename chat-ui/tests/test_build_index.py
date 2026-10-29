"""Tests for the build_index.py CLI, which builds and checks the committed index."""

import numpy as np

import build_index
import retrieval


def test_cli_rejects_missing_or_empty_model_access_key(monkeypatch, tmp_path, capsys):
    # Compose passes MODEL_ACCESS_KEY="" when .env lacks it; treat that as unset
    # rather than failing later with an opaque 401.
    for value in (None, "", "   "):
        if value is None:
            monkeypatch.delenv("MODEL_ACCESS_KEY", raising=False)
        else:
            monkeypatch.setenv("MODEL_ACCESS_KEY", value)

        code = build_index.main(["--source", str(tmp_path), "--out", str(tmp_path / "i.npz")])

        assert code == 1
        assert "MODEL_ACCESS_KEY" in capsys.readouterr().err


def _write_documents(root, guide="Ask for reasonable adjustments before the interview."):
    (root / "guides").mkdir(parents=True, exist_ok=True)
    (root / "guides" / "adjustments.md").write_text(f"# Adjustments\n\n{guide}\n")


def _save_index(root, path, *, embedding_model="qwen3-embedding-0.6b"):
    chunks = retrieval.chunk_documents(root)
    index = retrieval.Index(
        chunks,
        np.ones((len(chunks), 2), dtype=np.float32),
        embedding_model=embedding_model,
        source_hash="h",
        built_at="2026-10-29T00:00:00+00:00",
    )
    index.save(path)


def _check(source, out):
    return build_index.main(["--source", str(source), "--out", str(out), "--check"])


def test_check_passes_for_an_index_built_from_the_same_documents(monkeypatch, tmp_path, capsys):
    # The check calls no API, so it must not need the model key.
    monkeypatch.delenv("MODEL_ACCESS_KEY", raising=False)
    source, out = tmp_path / "kb", tmp_path / "kb_index.npz"
    _write_documents(source)
    _save_index(source, out)

    assert _check(source, out) == 0
    assert "up to date" in capsys.readouterr().out


def test_check_fails_when_a_document_changed(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("MODEL_ACCESS_KEY", raising=False)
    source, out = tmp_path / "kb", tmp_path / "kb_index.npz"
    _write_documents(source)
    _save_index(source, out)
    _write_documents(source, guide="Ask for adjustments at any stage of the process.")

    assert _check(source, out) == 1
    err = capsys.readouterr().err
    assert "out of date" in err
    assert "docker compose --profile tools run --rm index" in err


def test_check_fails_when_a_document_was_added(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("MODEL_ACCESS_KEY", raising=False)
    source, out = tmp_path / "kb", tmp_path / "kb_index.npz"
    _write_documents(source)
    _save_index(source, out)
    (source / "guides" / "gaps.md").write_text("# Gaps\n\nKeep explanations of CV gaps brief.\n")

    assert _check(source, out) == 1
    assert "out of date" in capsys.readouterr().err


def test_check_fails_when_the_embedding_model_differs(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("MODEL_ACCESS_KEY", raising=False)
    source, out = tmp_path / "kb", tmp_path / "kb_index.npz"
    _write_documents(source)
    _save_index(source, out, embedding_model="another-embedding-model")

    assert _check(source, out) == 1
    assert "another-embedding-model" in capsys.readouterr().err


def test_check_fails_when_the_index_is_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("MODEL_ACCESS_KEY", raising=False)
    source = tmp_path / "kb"
    _write_documents(source)

    assert _check(source, tmp_path / "missing.npz") == 1
    assert "No knowledge-base index" in capsys.readouterr().err
