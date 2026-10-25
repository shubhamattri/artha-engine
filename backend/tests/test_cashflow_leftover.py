"""`leftover` is what is AVAILABLE to spend; `spare` is what survives spending. Both, separately.

Redefining leftover as the spare figure looked like a fix and was a regression: cockpit's
safe_to_spend already subtracted spend from it, so the main screen double-subtracted and showed
-22,500. leftover is the closing row of an income waterfall and must stay the residual.
"""

from unittest.mock import patch


def _cashflow(monkeypatch, *, income, emi, fixed, invested, spent, sinking=0.0, insurance=0.0):
    """Drive compute_cashflow with stubbed collaborators so only the arithmetic is under test."""
    import api.cashflow as cf

    class _Line:
        def __init__(self, kind, amt):
            self.kind, self.amount_inr, self.label, self.id = kind, amt, kind, 1
            self.holder = self.start_date = self.end_date = None
            self.active = True

    lines = [_Line("income", income), _Line("fixed", fixed)]

    class _Q:
        def filter(self, *a, **k): return self
        def all(self): return lines

    class _DB:
        def query(self, *a, **k): return _Q()

    monkeypatch.setattr(cf, "_emi_total", lambda *a, **k: emi)
    with patch("api.recurring.compute_recurring", return_value={"takehome_outflow": invested}), \
         patch("api.sinking.compute_sinking", return_value={"monthly_set_aside": sinking}), \
         patch("api.insurance.compute_insurance", return_value={"annual_premium": insurance * 12}), \
         patch("api.budget.compute_budget", return_value={"net_spent_inr": spent}):
        return cf.compute_cashflow(_DB(), 1)


BASE = dict(income=500000.0, emi=150000.0, fixed=80000.0, invested=50000.0, insurance=2500.0)


def test_leftover_stays_the_waterfall_residual(monkeypatch):
    """A household's numbers (illustrative). leftover is the Rs 2,17,500 available to spend; spare is the
    Rs 97,500 that survives it. Consumers subtract spend from leftover themselves, so leftover
    must NOT already be net of it."""
    c = _cashflow(monkeypatch, **BASE, spent=120000.0)
    assert c["leftover"] == 217500.0
    assert c["spent"] == 120000.0
    assert c["spare"] == 97500.0


def test_leftover_closes_the_income_waterfall(monkeypatch):
    """cockpit renders income minus each committed bucket with leftover as the last row. If it does
    not close exactly, the breakdown stops summing to income."""
    c = _cashflow(monkeypatch, **BASE, spent=120000.0)
    assert (c["income"] - c["emi"] - c["fixed"] - c["saving"]
            - c["sinking"] - c["insurance"]) == c["leftover"]


def test_spending_more_shrinks_spare_but_never_leftover(monkeypatch):
    lean = _cashflow(monkeypatch, **BASE, spent=100000.0)
    heavy = _cashflow(monkeypatch, **BASE, spent=200000.0)
    assert lean["spare"] > heavy["spare"]
    assert heavy["spare"] == 17500.0
    assert lean["leftover"] == heavy["leftover"]      # the available figure does not move


def test_overspending_makes_spare_negative(monkeypatch):
    """Outspending what was available means something is being funded from savings. That must show
    as negative, never clamped to zero."""
    c = _cashflow(monkeypatch, **BASE, spent=300000.0)
    assert c["spare"] < 0
    assert c["spare"] == -82500.0
    assert c["leftover"] == 217500.0                 # unchanged: it is what WAS available


def test_underspending_leaves_more_spare(monkeypatch):
    c = _cashflow(monkeypatch, **BASE, spent=50000.0)
    assert c["spare"] == 167500.0


def test_a_budget_failure_does_not_take_down_cashflow(monkeypatch):
    """If the budget module raises, the page must still render. Leftover then falls back to the
    budget, which is the OLD wrong number, so it must be visibly flagged by spent == 0."""
    import api.cashflow as cf

    class _Line:
        def __init__(self, kind, amt):
            self.kind, self.amount_inr, self.label, self.id = kind, amt, kind, 1
            self.holder = self.start_date = self.end_date = None
            self.active = True

    lines = [_Line("income", 500000.0), _Line("fixed", 80000.0)]

    class _Q:
        def filter(self, *a, **k): return self
        def all(self): return lines

    class _DB:
        def query(self, *a, **k): return _Q()

    monkeypatch.setattr(cf, "_emi_total", lambda *a, **k: 150000.0)
    with patch("api.recurring.compute_recurring", return_value={"takehome_outflow": 50000.0}), \
         patch("api.sinking.compute_sinking", return_value={"monthly_set_aside": 0.0}), \
         patch("api.insurance.compute_insurance", return_value={"annual_premium": 30000.0}), \
         patch("api.budget.compute_budget", side_effect=RuntimeError("budget module down")):
        c = cf.compute_cashflow(_DB(), 1)
    assert c["spent"] == 0.0                      # the tell that spend could not be read
    assert c["spare"] == c["leftover"]            # degrades to the available figure
