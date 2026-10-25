"""`order` is the attack priority, so it must be what the money actually does.

It was the naive strategy sort. With a loan that refuses part payment those disagree: the
highest-rate loan sorts first and is never attacked first, so the "Attack Next" badge named one loan
while the plan timeline named another.

Note that `order`/`attack_next` and `steps` answer DIFFERENT questions and are allowed to differ:
attack_next is where spare cash goes first, steps is sorted by which loan CLOSES first. A large loan
can receive the money first and still finish last. The bug was that the badge showed neither — it
showed a rate sort that ignored whether the lender would take the money at all.
"""

from api.debt import _STRATEGIES, _simulate

AVALANCHE = _STRATEGIES[0][3]


def _l(name, bal, rate, emi, prepayment_rule="part", charge=0.0):
    return {"id": abs(hash(name)) % 9999, "name": name, "balance_inr": bal, "rate_pct": rate,
            "emi_inr": emi, "loan_type": "standard", "moratorium_months": 0,
            "post_moratorium_emi": None, "months_passed": 0,
            "prepayment_rule": prepayment_rule, "prepayment_charge_pct": charge}


def test_order_matches_the_plan_when_the_dearest_loan_cannot_be_attacked():
    loans = [_l("Dear but locked", 800_000, 9.35, 18_000, "foreclose_only", 3.0),
             _l("Cheaper and open", 200_000, 9.10, 7_000)]
    sim = _simulate(loans, AVALANCHE, extra=100_000)
    assert sim["attack_next"] == "Cheaper and open"     # NOT the 9.35% one
    assert sim["order"][0]["name"] == "Cheaper and open"


def test_order_still_follows_the_rate_when_everything_is_attackable():
    loans = [_l("Dearest", 300_000, 12.0, 10_000), _l("Middle", 300_000, 10.0, 10_000)]
    sim = _simulate(loans, AVALANCHE, extra=50_000)
    assert sim["order"][0]["name"] == "Dearest"
    assert sim["attack_next"] == "Dearest"


def test_attack_next_is_always_the_head_of_the_order_in_every_strategy():
    """The invariant the bug broke: the badge and the ordered list are the same answer. They are NOT
    required to match steps[0], which is sorted by what closes first."""
    loans = [_l("Locked", 800_000, 9.35, 18_000, "foreclose_only", 3.0),
             _l("Open A", 200_000, 9.10, 7_000), _l("Open B", 500_000, 8.0, 12_000)]
    for _key, _label, _desc, sort_key in _STRATEGIES:
        sim = _simulate(loans, sort_key, extra=100_000)
        assert sim["attack_next"] == sim["order"][0]["name"], _label
        assert sim["attack_next"] != "Locked", _label      # never the loan that refuses the money


def test_a_loan_the_pool_never_reaches_sorts_last():
    loans = [_l("Never funded", 900_000, 15.0, 5_000, "none"), _l("Open", 200_000, 8.0, 7_000)]
    sim = _simulate(loans, AVALANCHE, extra=50_000)
    assert sim["order"][-1]["name"] == "Never funded"
    assert sim["attack_next"] == "Open"


def test_the_option_actually_carries_attack_next(db_session):
    """_simulate having a key is not the same as the ROUTE publishing it. The forward schedule shipped
    returning zero rows for exactly this reason: helper tested, route not."""
    from datetime import date as _d

    from api.debt import compute_debt
    from models.household import Loan
    from models.tenant import Household

    h = Household(name="A"); db_session.add(h); db_session.commit()
    db_session.add_all([
        Loan(household_id=h.id, name="Dear locked", lender="X", holder="A", category="Car",
             original_principal=800_000, annual_rate_pct=9.35, tenure_months=60,
             disbursement_date=_d(2024, 4, 9), rate_type="fixed"),
        Loan(household_id=h.id, name="Cheap open", lender="Y", holder="A", category="Home",
             original_principal=200_000, annual_rate_pct=9.10, tenure_months=120,
             disbursement_date=_d(2024, 4, 9), rate_type="floating"),
    ])
    db_session.commit()

    for o in compute_debt(db_session, h.id)["strategies"]["options"]:
        assert "attack_next" in o, f"{o['key']} does not publish attack_next"
        if o["order"]:
            assert o["attack_next"] == o["order"][0]["name"], o["key"]
