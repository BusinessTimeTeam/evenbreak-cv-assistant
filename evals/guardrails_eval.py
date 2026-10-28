"""Check the guardrails against labelled messages and CVs.

Runs every case in evals/guardrail_cases.json through the real classifier checks
(guardrails.check_message / guardrails.check_cv, so it calls the model with the
MODEL_ACCESS_KEY in .env) and scores the verdicts against the labels.

    python guardrails_eval.py                # every case -> evals/results/<ts>/guardrails.json
    python guardrails_eval.py --case ID      # one case only

The number that matters is false blocks: an allow case that was refused means a
genuine job-seeker would be turned away, so any false block exits 1. Missed
blocks (misuse let through) are reported but tolerated. Run through docker
compose (see docker-compose.yml, service "evals").
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import guardrails
from inference import InferenceConfig

EVALS_DIR = Path(__file__).parent
CASES_PATH = EVALS_DIR / "guardrail_cases.json"
# CV fixtures: the guardrail-specific ones first, then the CVs compare.py uses.
CV_DIRS = [EVALS_DIR / "guardrail-cvs", EVALS_DIR / "cvs"]
RESULTS_DIR = EVALS_DIR / "results"
# Small cases, so a few at once; kept low to stay clear of rate limits.
CONCURRENCY = 4


def cv_text(name: str) -> str:
    for folder in CV_DIRS:
        path = folder / name
        if path.exists():
            return path.read_text()
    raise SystemExit(f"no CV fixture named {name!r}")


async def run_case(case: dict, config: InferenceConfig) -> dict:
    if case["kind"] == "cv":
        verdict = await guardrails.check_cv(config, cv_text(case["file"]))
    else:
        verdict = await guardrails.check_message(config, case["message"], case.get("history", []))
    return {"id": case["id"], "expect": case["expect"], "allowed": verdict.allowed, "reason": verdict.reason}


def score(results: list[dict]) -> dict:
    return {
        "false_blocks": [r["id"] for r in results if r["expect"] == "allow" and not r["allowed"]],
        "missed_blocks": [r["id"] for r in results if r["expect"] == "block" and r["allowed"]],
        "total": len(results),
    }


def _line(result: dict) -> str:
    actual = "allow" if result["allowed"] else "block"
    status = "PASS" if actual == result["expect"] else "FAIL"
    return f"{status} {result['id']} {result['expect']}→{actual} {result['reason'] or ''}".rstrip()


def _load_cases(case_id: str | None) -> list[dict]:
    cases = json.loads(CASES_PATH.read_text())
    if case_id:
        cases = [c for c in cases if c["id"] == case_id]
        if not cases:
            raise SystemExit(f"no case with id {case_id!r}")
    return cases


async def _run(args) -> int:
    cases = _load_cases(args.case)
    config = InferenceConfig.from_env()
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def bounded(case):
        async with semaphore:
            return await run_case(case, config)

    results = await asyncio.gather(*(bounded(case) for case in cases))
    for result in results:
        print(_line(result))
    scored = score(results)
    print(
        f"\n{scored['total']} cases: {len(scored['false_blocks'])} false blocks "
        f"{scored['false_blocks']}, {len(scored['missed_blocks'])} missed blocks {scored['missed_blocks']}"
    )
    out = RESULTS_DIR / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    (out / "guardrails.json").write_text(json.dumps({"score": scored, "results": results}, indent=2) + "\n")
    print(f"wrote {out / 'guardrails.json'}")
    return 1 if scored["false_blocks"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the guardrails against labelled cases.")
    parser.add_argument("--case", help="run a single case by id")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
