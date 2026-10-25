"""The money-move engine — deterministic `decide()` must pick the right move and own the numbers.
Pure function, no DB. Narration is tested only for its fallback (no AI key in CI)."""

from services import money_moves


def _sc(**kw):
    base = {"overspend": 0, "actionable": 0, "over_budget": 0, "surplus": 0, "data_ok": True}
    base.update(kw)
    return base


def test_cash_shortfall_beats_everything():
    m = money_moves.decide(_sc(overspend=9000, actionable=0, over_budget=45000),
                           {"covered": True, "non_loan_total": 9000, "shortfall": 0, "options": []}, None)
    assert m["kind"] == "cover_overspend"
    assert m["facts"]["overspend"] == 9000 and m["facts"]["covered"] is True
    assert "9,000" in m["headline"] and "prepay" in m["headline"].lower()


def test_spare_plus_loan_is_attack_debt():
    step = {"name": "HDFC CC", "monthly_payment": 45000, "own_emi": 8000}
    m = money_moves.decide(_sc(actionable=37000), None, step)
    assert m["kind"] == "attack_debt" and m["target"] is step
    assert m["facts"]["loan"] == "HDFC CC" and m["facts"]["actionable"] == 37000
    assert "HDFC CC" in m["headline"]


def test_under_savings_target_is_hold():
    m = money_moves.decide(_sc(over_budget=45000, surplus=37000), None, None)
    assert m["kind"] == "hold" and m["facts"]["surplus"] == 37000


def test_nothing_pressing_is_on_track():
    assert money_moves.decide(_sc(surplus=0), None, None)["kind"] == "on_track"


def test_broken_budget_data_is_surfaced_not_faked():
    m = money_moves.decide(_sc(data_ok=False, overspend=999), None, None)
    assert m["kind"] == "unknown" and "Plan" in m["headline"]


def test_narrate_on_track_never_calls_ai():
    m = money_moves.decide(_sc(), None, None)          # on_track short-circuits to the headline
    assert money_moves.narrate(m) == m["headline"]


def test_narrate_falls_back_when_ai_unavailable(monkeypatch):
    from services import llm
    monkeypatch.setattr(llm, "available", lambda: False)   # force the no-AI path
    m = money_moves.decide(_sc(over_budget=5000, surplus=5000), None, None)
    assert money_moves.narrate(m) == m["headline"]         # numbers stay exact, never blocked on a model
