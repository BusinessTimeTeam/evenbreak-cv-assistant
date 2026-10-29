"""Build the knowledge-base index file, or check that it's up to date.

Usage:
    python build_index.py --source DIR --out FILE
    python build_index.py --source DIR --out FILE --check

The index is committed to the repository (knowledge-base/index/kb_index.npz)
and copied into the image, so it's rebuilt only when the knowledge base
changes:

    docker compose --profile tools run --rm index

Building reads MODEL_ACCESS_KEY (and optionally EMBEDDING_MODEL /
INFERENCE_BASE_URL) from the environment. --check calls no API: it re-chunks
the documents and compares them, and the embedding model, with the index, so
CI can fail a deploy whose committed index is out of date.
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from inference import InferenceConfig, InferenceError
from retrieval import REBUILD_HINT, Index, IndexLoadError, build_index, chunk_documents


def check(source: Path, out: Path) -> int:
    embedding_model = os.environ.get("EMBEDDING_MODEL") or InferenceConfig(api_key="").embedding_model
    try:
        index = Index.load(out, expected_embedding_model=embedding_model)
    except IndexLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    expected, actual = chunk_documents(source), index.chunks
    if expected != actual:
        first = next(
            (i for i, (e, a) in enumerate(zip(expected, actual)) if e != a),
            min(len(expected), len(actual)),
        )
        changed = (expected[first] if first < len(expected) else actual[first]).source
        print(
            f"error: the knowledge-base index at {out} is out of date: the documents give "
            f"{len(expected)} chunks, the index has {len(actual)}, first difference in "
            f"{changed}. {REBUILD_HINT}",
            file=sys.stderr,
        )
        return 1
    print(f"Index is up to date: {len(actual)} chunks with {index.embedding_model} ({out})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the knowledge-base index file.")
    parser.add_argument("--source", type=Path, required=True, help="knowledge-base data-sources directory")
    parser.add_argument("--out", type=Path, required=True, help="index file to write (.npz)")
    parser.add_argument(
        "--check", action="store_true", help="check the index matches the documents instead of building it"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)

    if args.check:
        return check(args.source, args.out)

    try:
        config = InferenceConfig.from_env()
    except KeyError:
        config = None
    if config is None or not config.api_key.strip():
        print("error: MODEL_ACCESS_KEY is not set (needed to embed the knowledge base)", file=sys.stderr)
        return 1

    started = time.monotonic()
    try:
        index = asyncio.run(build_index(args.source, config))
    except (InferenceError, ValueError) as exc:
        print(f"error: index build failed: {exc}", file=sys.stderr)
        return 1
    index.save(args.out)
    print(
        f"Indexed {len(index)} chunks with {index.embedding_model} "
        f"in {time.monotonic() - started:.1f}s -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
