"""The knowledge-base index: build it, save/load it, and search it.

The index is a single .npz file holding every chunk's text, its source path and
its embedding, plus metadata naming the embedding model that built it. Search is
brute-force cosine similarity in memory, which stays fast up to tens of
thousands of chunks. Callers only use ``Index`` and ``build_index``, so the
storage can be swapped (pgvector, a vector database) without touching them.

This module is copied into the Docker ``index`` stage, so it may only import
chunking and inference from the app.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from chunking import Chunk, chunk_document, load_documents
from inference import InferenceConfig, embed

REBUILD_HINT = "Rebuild it with `docker compose build chat-ui` (or run build_index.py)."


class IndexLoadError(Exception):
    """The index file is missing, unreadable, or unusable."""


class EmbeddingModelMismatch(IndexLoadError):
    """The index was built with a different embedding model than queries use."""


@dataclass(frozen=True)
class ScoredChunk:
    source: str
    text: str
    score: float


def _normalise(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


class Index:
    def __init__(
        self,
        chunks: list[Chunk],
        vectors: np.ndarray,
        *,
        embedding_model: str,
        source_hash: str,
        built_at: str,
    ):
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim != 2 or len(vectors) != len(chunks):
            raise ValueError(f"expected {len(chunks)} vectors, got shape {vectors.shape}")
        self._chunks = list(chunks)
        self._vectors = _normalise(vectors)
        self._embedding_model = embedding_model
        self._source_hash = source_hash
        self._built_at = built_at

    @property
    def embedding_model(self) -> str:
        return self._embedding_model

    @property
    def source_hash(self) -> str:
        return self._source_hash

    def __len__(self) -> int:
        return len(self._chunks)

    def search(self, query_vectors: Sequence[Sequence[float]], k: int = 10) -> list[ScoredChunk]:
        """Top-k chunks across all queries, each chunk scored by its best match."""
        queries = _normalise(np.asarray(query_vectors, dtype=np.float32).reshape(-1, self._vectors.shape[1]))
        best = (queries @ self._vectors.T).max(axis=0)
        order = np.argsort(-best, kind="stable")[:k]
        return [
            ScoredChunk(self._chunks[i].source, self._chunks[i].text, float(best[i]))
            for i in order
        ]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = {
            "embedding_model": self._embedding_model,
            "dim": int(self._vectors.shape[1]),
            "source_hash": self._source_hash,
            "built_at": self._built_at,
            "count": len(self._chunks),
        }
        with path.open("wb") as fh:
            np.savez_compressed(
                fh,
                vectors=self._vectors,
                sources=np.array([c.source for c in self._chunks], dtype=np.str_),
                texts=np.array([c.text for c in self._chunks], dtype=np.str_),
                metadata=np.array(json.dumps(metadata)),
            )

    @classmethod
    def load(cls, path: Path, *, expected_embedding_model: str) -> "Index":
        try:
            with np.load(path, allow_pickle=False) as data:
                metadata = json.loads(str(data["metadata"]))
                chunks = [Chunk(str(s), str(t)) for s, t in zip(data["sources"], data["texts"])]
                vectors = data["vectors"]
        except FileNotFoundError as exc:
            raise IndexLoadError(f"No knowledge-base index at {path}. {REBUILD_HINT}") from exc
        except (OSError, ValueError, KeyError) as exc:
            raise IndexLoadError(f"Could not read the knowledge-base index at {path} ({exc}). {REBUILD_HINT}") from exc

        if metadata.get("embedding_model") != expected_embedding_model:
            raise EmbeddingModelMismatch(
                f"The index at {path} was built with embedding model "
                f"{metadata.get('embedding_model')!r}, but queries use {expected_embedding_model!r}. "
                f"{REBUILD_HINT}"
            )
        return cls(
            chunks,
            vectors,
            embedding_model=metadata["embedding_model"],
            source_hash=metadata["source_hash"],
            built_at=metadata["built_at"],
        )


def source_hash(documents: list[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for source, text in sorted(documents):
        digest.update(source.encode())
        digest.update(b"\0")
        digest.update(text.encode())
        digest.update(b"\0")
    return digest.hexdigest()


async def build_index(root: Path, config: InferenceConfig) -> Index:
    documents = load_documents(root)
    chunks = [chunk for source, text in documents for chunk in chunk_document(source, text)]
    # Prefix the file name so it contributes to matching; store the plain text.
    vectors = await embed(config, [f"{c.source}\n{c.text}" for c in chunks])
    return Index(
        chunks,
        np.array(vectors, dtype=np.float32),
        embedding_model=config.embedding_model,
        source_hash=source_hash(documents),
        built_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
