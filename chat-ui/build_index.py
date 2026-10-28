"""Build the knowledge-base index file.

Usage:
    python build_index.py --source DIR --out FILE

Reads BUILD_MODEL_ACCESS_KEY, the embedding-only build key (and optionally EMBEDDING_MODEL / INFERENCE_BASE_URL)
from the environment. Runs in the Docker ``index`` stage on every image build.
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from inference import InferenceConfig, InferenceError
from retrieval import build_index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the knowledge-base index file.")
    parser.add_argument("--source", type=Path, required=True, help="knowledge-base data-sources directory")
    parser.add_argument("--out", type=Path, required=True, help="index file to write (.npz)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)

    try:
        config = InferenceConfig.from_env("BUILD_MODEL_ACCESS_KEY")
    except KeyError:
        config = None
    if config is None or not config.api_key.strip():
        print("error: BUILD_MODEL_ACCESS_KEY is not set (the embedding-only build key, needed to embed the knowledge base)", file=sys.stderr)
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
