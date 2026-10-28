"""Compare the chat UI's answers with the old managed agent's.

Runs every case in evals/cases.json through the running chat UI and writes a
side-by-side markdown report against the old agent's answers, for a person to
judge. There is no automatic scoring.

    python compare.py                # chat UI + baseline -> evals/results/<ts>/
    python compare.py --case ID      # one case only

The old agent has been deleted, so its answers are a frozen baseline
(evals/baseline/old-agent.json). The chat UI is called through /api/upload and
/api/chat, so it is tested end to end. Run through docker compose (see
docker-compose.yml, service "evals").
"""

import argparse
import asyncio
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from docx import Document

EVALS_DIR = Path(__file__).parent
CASES_PATH = EVALS_DIR / "cases.json"
CVS_DIR = EVALS_DIR / "cvs"
BASELINE_PATH = EVALS_DIR / "baseline" / "old-agent.json"
RESULTS_DIR = EVALS_DIR / "results"
TIMEOUT = httpx.Timeout(180.0)


def _cv_text(case: dict, cvs_dir: Path) -> str | None:
    return (cvs_dir / case["cv"]).read_text() if case.get("cv") else None


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
    old = json.loads(BASELINE_PATH.read_text())["cases"]
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
    parser = argparse.ArgumentParser(description="Compare the chat UI with the old agent's answers.")
    parser.add_argument("--case", help="run a single case by id")
    parser.add_argument("--base-url", default="http://chat-ui:8080", help="the new chat UI")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
