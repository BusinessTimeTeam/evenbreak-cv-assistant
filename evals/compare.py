"""Compare the old managed agent with the new app-side retrieval pipeline.

Runs every case in evals/cases.json through both systems and writes a
side-by-side markdown report for a person to judge. There is no automatic
scoring.

    python compare.py --baseline     # old agent -> evals/baseline/old-agent.json
    python compare.py                # new pipeline + baseline -> evals/results/<ts>/
    python compare.py --case ID      # one case only

The old agent is called exactly as the old chat UI called it: the CV as a
leading user message, then the history, then the turn. The new pipeline is
called through the running chat UI's /api/upload and /api/chat, so it is tested
end to end. Run through docker compose (see docker-compose.yml, service
"evals").
"""

import argparse
import asyncio
import io
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from docx import Document

from main import _cv_message

EVALS_DIR = Path(__file__).parent
CASES_PATH = EVALS_DIR / "cases.json"
CVS_DIR = EVALS_DIR / "cvs"
BASELINE_PATH = EVALS_DIR / "baseline" / "old-agent.json"
RESULTS_DIR = EVALS_DIR / "results"
TIMEOUT = httpx.Timeout(180.0)


def _cv_text(case: dict, cvs_dir: Path) -> str | None:
    return (cvs_dir / case["cv"]).read_text() if case.get("cv") else None


def _client(client: httpx.AsyncClient | None) -> httpx.AsyncClient:
    return client or httpx.AsyncClient(timeout=TIMEOUT)


async def run_old(case: dict, cvs_dir: Path, *, client: httpx.AsyncClient | None = None) -> list[dict]:
    endpoint, key = os.environ["AGENT_ENDPOINT"], os.environ["AGENT_API_KEY"]
    cv_text = _cv_text(case, cvs_dir)
    prefix = [_cv_message({"filename": case["cv"], "text": cv_text})] if cv_text else []
    history, results = [], []
    async with _client(client) as http:
        for turn in case["turns"]:
            messages = prefix + history + [{"role": "user", "content": turn}]
            try:
                resp = await http.post(
                    endpoint, json={"messages": messages}, headers={"Authorization": f"Bearer {key}"}
                )
                resp.raise_for_status()
                answer = resp.json()["choices"][0]["message"]["content"] or ""
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                answer = f"Error: {exc}"
            results.append({"turn": turn, "answer": answer})
            history += [{"role": "user", "content": turn}, {"role": "assistant", "content": answer}]
    return results


def _docx(text: str) -> bytes:
    document = Document()
    for line in text.splitlines():
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


async def run_new(
    case: dict, cvs_dir: Path, base_url: str, *, client: httpx.AsyncClient | None = None
) -> list[dict]:
    cv_text = _cv_text(case, cvs_dir)
    history, results = [], []
    async with client or httpx.AsyncClient(base_url=base_url, timeout=TIMEOUT) as http:
        session_id = None
        if cv_text:
            filename = Path(case["cv"]).with_suffix(".docx").name
            resp = await http.post("/api/upload", files={"file": (filename, _docx(cv_text))})
            resp.raise_for_status()
            session_id = resp.json()["session_id"]
        for turn in case["turns"]:
            payload = {"message": turn, "history": history, "session_id": session_id}
            resp = await http.post("/api/chat", json=payload)
            resp.raise_for_status()
            body = resp.json()
            answer = body.get("content", "")
            results.append(
                {"turn": turn, "answer": answer, "sources": [s["source"] for s in body.get("sources", [])]}
            )
            history = history + [{"role": "user", "content": turn}, {"role": "assistant", "content": answer}]
    return results


def render_report(cases: list[dict], old: dict, new: dict) -> str:
    lines = ["# Old agent vs new pipeline", ""]
    for case in cases:
        lines += [f"## {case['id']}", "", f"**CV:** {case.get('cv') or 'none'}  "]
        if case.get("notes"):
            lines += [f"**What a good answer draws on:** {case['notes']}"]
        lines.append("")
        old_turns = old.get(case["id"], [])
        for n, new_turn in enumerate(new.get(case["id"], []), start=1):
            old_answer = old_turns[n - 1]["answer"] if n <= len(old_turns) else "_(no baseline answer)_"
            sources = ", ".join(f"`{s}`" for s in new_turn.get("sources", [])) or "_none_"
            lines += [
                f"### Turn {n}: {new_turn['turn']}",
                "",
                "**Old agent**",
                "",
                old_answer,
                "",
                "**New pipeline**",
                "",
                new_turn["answer"],
                "",
                f"_Retrieved: {sources}_",
                "",
            ]
    return "\n".join(lines)


def _load_cases(case_id: str | None) -> list[dict]:
    cases = json.loads(CASES_PATH.read_text())
    if case_id:
        cases = [c for c in cases if c["id"] == case_id]
        if not cases:
            raise SystemExit(f"no case with id {case_id!r}")
    return cases


async def _run(args) -> int:
    cases = _load_cases(args.case)
    if args.baseline:
        existing = json.loads(BASELINE_PATH.read_text())["cases"] if BASELINE_PATH.exists() and args.case else {}
        for case in cases:
            print(f"old agent: {case['id']}", flush=True)
            existing[case["id"]] = await run_old(case, CVS_DIR)
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps({"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "cases": existing}, indent=2)
            + "\n"
        )
        print(f"wrote {BASELINE_PATH}")
        return 0

    old = json.loads(BASELINE_PATH.read_text())["cases"] if BASELINE_PATH.exists() else {}
    if not old:
        print("warning: no baseline yet; run with --baseline first", file=sys.stderr)
    new = {}
    for case in cases:
        print(f"new pipeline: {case['id']}", flush=True)
        new[case["id"]] = await run_new(case, CVS_DIR, args.base_url)
    out = RESULTS_DIR / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    (out / "new.json").write_text(json.dumps({"cases": new}, indent=2) + "\n")
    (out / "report.md").write_text(render_report(cases, old, new))
    print(f"wrote {out / 'report.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare the old agent with the new pipeline.")
    parser.add_argument("--baseline", action="store_true", help="run the old agent and save the baseline")
    parser.add_argument("--case", help="run a single case by id")
    parser.add_argument("--base-url", default="http://chat-ui:8080", help="the new chat UI")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
