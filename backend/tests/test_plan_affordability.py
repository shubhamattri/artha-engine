"""A plan that asks for more in a month than the household has is not a plan.

"Pay Rs 2 lakhs a month" reads as impossible against a Rs 1 lakh surplus. It is not: it is that
loan's own EMI plus the EMIs of loans that have already closed. But a headline shown without WHEN it
starts and WHAT funds it is indistinguishable from an error, so both are now carried on the step and
the arithmetic is asserted rather than assumed.
"""

from api.debt import _STRATEGIES, _simulate

AVALANCHE = _STRATEGIES[0][3]


def _l(name, bal, rate, emi):
    return {"id": abs(hash(name)) % 9999, "name": name, "balance_inr": bal, "rate_pct": rate,
            "emi_inr": emi, "loan_type": "standard", "moratorium_months": 0,
            "post_moratorium_emi": None, "months_passed": 0}


LOANS = [_l("A", 300_000, 12.0, 10_000), _l("B", 600_000, 10.0, 15_000),
         _l("C", 900_000, 8.0, 20_000)]


def test_no_step_ever_asks_for_more_than_the_household_has():
    sim = _simulate(LOANS, AVALANCHE, extra=50_000)
    assert sim["exceeds_capacity"] == []
    assert sim["monthly_capacity"] == 45_000 + 50_000     # every EMI plus the surplus
    for st in sim["steps"]:
        assert st["monthly_payment"] <= sim["monthly_capacity"] + 1


def test_a_later_step_says_which_month_its_headline_starts():
    """Without this the figure reads as something to do today."""
    sim = _simulate(LOANS, AVALANCHE, extra=50_000)
    last = sim["steps"][-1]
    assert last["peak_from_month"] is not None
    assert last["peak_from_month"] > 1                    # only reachable after earlier loans close


def test_a_later_step_names_the_loans_that_funded_it():
    sim = _simulate(LOANS, AVALANCHE, extra=50_000)
    last = sim["steps"][-1]
    assert "A" in last["funded_by_closing"] and "B" in last["funded_by_closing"]
    assert last["name"] not in last["funded_by_closing"]  # never credits itself


def test_the_first_target_is_funded_by_nothing_yet():
    sim = _simulate(LOANS, AVALANCHE, extra=50_000)
    first = sim["steps"][0]
    assert first["funded_by_closing"] == []
    assert first["peak_from_month"] == 1
    assert first["monthly_payment"] == first["own_emi"] + first["from_pool"]


def test_the_headline_is_always_its_own_emi_plus_the_pool():
    """The split has to reconcile, or the funding explanation is fiction."""
    sim = _simulate(LOANS, AVALANCHE, extra=50_000)
    for st in sim["steps"]:
        assert abs(st["monthly_payment"] - (st["own_emi"] + st["from_pool"])) < 1
