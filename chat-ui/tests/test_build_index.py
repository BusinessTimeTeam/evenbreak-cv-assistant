"""Tests for the build_index.py CLI that runs in the Docker index stage."""

import build_index


def test_cli_rejects_missing_or_empty_build_key(monkeypatch, tmp_path, capsys):
    # Compose passes BUILD_MODEL_ACCESS_KEY="" when .env lacks it; treat that as unset
    # rather than failing later with an opaque 401.
    # The runtime key must not satisfy the build: the two keys are scoped differently.
    monkeypatch.delenv("MODEL_ACCESS_KEY", raising=False)
    for value in (None, "", "   "):
        if value is None:
            monkeypatch.delenv("BUILD_MODEL_ACCESS_KEY", raising=False)
        else:
            monkeypatch.setenv("BUILD_MODEL_ACCESS_KEY", value)

        code = build_index.main(["--source", str(tmp_path), "--out", str(tmp_path / "i.npz")])

        assert code == 1
        assert "BUILD_MODEL_ACCESS_KEY" in capsys.readouterr().err
