"""The LLM judge fails closed: anything short of a parseable, passing verdict is a fail.

The model is stubbed at `services.llm`, so no request leaves the machine.
"""

from __future__ import annotations

import json

import pytest

from evals import judge
from services import llm

Q = "Can I buy a ₹3,000 briefcase?"
A = "You have ₹5,000 actually free this cycle, so yes."
CALLS = [{"name": "actually_free", "input": {"lens": "cycle"}, "ok": True}]


def _model_says(monkeypatch, raw):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "complete", lambda *a, **k: raw)


def _verdict(faith=0.9, conservative=True, asks=True, reason="grounded"):
    return json.dumps({"faithfulness": faith, "not_too_conservative": conservative,
                       "asks_for_missing_inputs": asks, "reason": reason})


def test_no_api_key_is_an_error_not_a_pass(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    r = judge.score(Q, A, CALLS)
    assert r["pass"] is False and r["is_error"] is True and r["score"] == 0.0


def test_a_failed_call_is_an_error_not_a_pass(monkeypatch):
    _model_says(monkeypatch, None)
    r = judge.score(Q, A, CALLS)
    assert r["pass"] is False and r["is_error"] is True


def test_unparseable_output_is_an_error_not_a_pass(monkeypatch):
    _model_says(monkeypatch, "Looks fine to me!")
    r = judge.score(Q, A, CALLS)
    assert r["pass"] is False and r["is_error"] is True


def test_an_empty_answer_is_never_judged(monkeypatch):
    called = []
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "complete", lambda *a, **k: called.append(1) or _verdict())
    r = judge.score(Q, "   ", CALLS)
    assert r["pass"] is False and r["is_error"] is True and called == []


def test_a_grounded_verdict_passes_with_the_weighted_score(monkeypatch):
    _model_says(monkeypatch, _verdict(faith=0.9))
    r = judge.score(Q, A, CALLS)
    assert r["pass"] is True and r["is_error"] is False
    assert r["score"] == pytest.approx(0.9 * 0.60 + 0.25 + 0.15)


@pytest.mark.parametrize("faith", [0.79, 0.0])
def test_faithfulness_below_the_threshold_fails(monkeypatch, faith):
    _model_says(monkeypatch, _verdict(faith=faith))
    r = judge.score(Q, A, CALLS)
    assert r["pass"] is False and r["is_error"] is False


def test_faithfulness_at_the_threshold_passes(monkeypatch):
    _model_says(monkeypatch, _verdict(faith=judge.FAITHFULNESS_PASS_THRESHOLD))
    assert judge.score(Q, A, CALLS)["pass"] is True


def test_any_failed_sub_check_fails_the_turn(monkeypatch):
    _model_says(monkeypatch, _verdict(conservative=False))
    assert judge.score(Q, A, CALLS)["pass"] is False
    _model_says(monkeypatch, _verdict(asks=False))
    assert judge.score(Q, A, CALLS)["pass"] is False


def test_a_fenced_json_verdict_is_still_read(monkeypatch):
    _model_says(monkeypatch, "```json\n" + _verdict() + "\n```")
    r = judge.score(Q, A, CALLS)
    assert r["is_error"] is False and r["pass"] is True


def test_out_of_range_faithfulness_is_clamped(monkeypatch):
    _model_says(monkeypatch, _verdict(faith=7.0))
    assert judge.score(Q, A, CALLS)["faithfulness"] == 1.0


def test_braces_in_user_text_do_not_break_prompt_assembly(monkeypatch):
    _model_says(monkeypatch, _verdict())
    r = judge.score("what is {pool} vs {free}?", "pool {x} is not free {}", CALLS)
    assert r["is_error"] is False
