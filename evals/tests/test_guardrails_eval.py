"""Tests for the guardrail eval runner. The classifier checks are stubbed."""

import json

import pytest

import guardrails_eval
from guardrails import Verdict

CASES = [
    {"id": "hello", "kind": "message", "expect": "allow", "message": "Hi", "history": []},
    {
        "id": "shorter",
        "kind": "message",
        "expect": "allow",
        "message": "can you make it shorter?",
        "history": [{"role": "user", "content": "Write my CV summary"}, {"role": "assistant", "content": "Here it is"}],
    },
    {"id": "poem", "kind": "message", "expect": "block", "message": "Write a poem about the sea", "history": []},
    {"id": "good-cv", "kind": "cv", "expect": "allow", "file": "good.md"},
]


def _result(id_, expect, allowed, reason=None):
    return {"id": id_, "expect": expect, "allowed": allowed, "reason": reason}


def test_score_counts_a_false_block():
    scored = guardrails_eval.score(
        [_result("a", "allow", False, "off_topic"), _result("b", "block", False, "injection")]
    )

    assert scored == {"false_blocks": ["a"], "missed_blocks": [], "total": 2}


def test_score_counts_a_missed_block():
    scored = guardrails_eval.score([_result("a", "allow", True), _result("b", "block", True)])

    assert scored == {"false_blocks": [], "missed_blocks": ["b"], "total": 2}


def test_score_all_pass():
    scored = guardrails_eval.score([_result("a", "allow", True), _result("b", "block", False, "off_topic")])

    assert scored == {"false_blocks": [], "missed_blocks": [], "total": 2}


@pytest.fixture
def stubbed(tmp_path, monkeypatch):
    """Point the runner at temporary cases, fixtures and results, and stub the checks."""
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(CASES))
    cvs_dir = tmp_path / "guardrail-cvs"
    cvs_dir.mkdir()
    (cvs_dir / "good.md").write_text("Alex Example\nStore Manager")
    monkeypatch.setattr(guardrails_eval, "CASES_PATH", cases_path)
    monkeypatch.setattr(guardrails_eval, "CV_DIRS", [cvs_dir])
    monkeypatch.setattr(guardrails_eval, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setenv("MODEL_ACCESS_KEY", "test-key")

    calls = {"message": [], "cv": []}

    async def fake_check_message(config, message, history):
        calls["message"].append((message, history))
        return Verdict(True) if message != "Hi" else Verdict(False, "off_topic")

    async def fake_check_cv(config, text):
        calls["cv"].append(text)
        return Verdict(True)

    monkeypatch.setattr(guardrails_eval.guardrails, "check_message", fake_check_message)
    monkeypatch.setattr(guardrails_eval.guardrails, "check_cv", fake_check_cv)
    return tmp_path, calls


def test_case_filter_runs_only_that_case(stubbed):
    tmp_path, calls = stubbed

    code = guardrails_eval.main(["--case", "shorter"])

    assert code == 0
    assert calls["message"] == [("can you make it shorter?", CASES[1]["history"])]
    assert calls["cv"] == []
    [run] = list((tmp_path / "results").iterdir())
    saved = json.loads((run / "guardrails.json").read_text())
    assert [r["id"] for r in saved["results"]] == ["shorter"]


def test_unknown_case_id_exits_with_an_error(stubbed):
    with pytest.raises(SystemExit, match="no case with id 'nope'"):
        guardrails_eval.main(["--case", "nope"])


def test_full_run_reads_cv_files_prints_lines_and_exits_1_on_a_false_block(stubbed, capsys):
    tmp_path, calls = stubbed

    code = guardrails_eval.main([])

    assert code == 1  # "hello" is an allow case the stub blocks
    assert calls["cv"] == ["Alex Example\nStore Manager"]
    out = capsys.readouterr().out
    assert "FAIL hello allow→block off_topic" in out
    assert "PASS good-cv allow→allow" in out
    assert "FAIL poem block→allow" in out
    [run] = list((tmp_path / "results").iterdir())
    saved = json.loads((run / "guardrails.json").read_text())
    assert saved["score"] == {"false_blocks": ["hello"], "missed_blocks": ["poem"], "total": 4}


def test_cv_file_falls_back_to_the_shared_cvs_folder(stubbed, monkeypatch):
    tmp_path, calls = stubbed
    shared = tmp_path / "cvs"
    shared.mkdir()
    (shared / "shared.md").write_text("Shared CV")
    monkeypatch.setattr(guardrails_eval, "CV_DIRS", [tmp_path / "guardrail-cvs", shared])

    assert guardrails_eval.cv_text("shared.md") == "Shared CV"
    assert guardrails_eval.cv_text("good.md") == "Alex Example\nStore Manager"
