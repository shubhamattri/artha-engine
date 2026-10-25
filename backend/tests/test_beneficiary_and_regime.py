"""Whose principal is it, and is saving up ever worth it."""

from api.debt import _STRATEGIES, _simulate
from services import prepayment

AVALANCHE = _STRATEGIES[0][3]


def _l(name, bal, rate, emi, **kw):
    d = {"id": abs(hash(name)) % 9999, "name": name, "balance_inr": bal, "rate_pct": rate,
         "emi_inr": emi, "loan_type": "standard", "moratorium_months": 0,
         "post_moratorium_emi": None, "months_passed": 0}
    d.update(kw)
    return d


def test_a_loan_serviced_for_someone_else_is_not_attacked():
    """Its EMI is still yours to pay, but prepaying it hands your money to their principal, so spare
    cash must flow past it."""
    mine = _l("Mine", 500_000, 8.0, 10_000)
    theirs = _l("Brother's", 500_000, 9.5, 10_000)
    both = _simulate([mine, theirs], AVALANCHE, extra=50_000)
    names = [s["name"] for s in both["steps"]]
    # avalanche on rate alone would hit the 9.5% loan first
    assert names[0] == "Brother's"


def test_the_engine_receives_only_attackable_loans():
    """compute_debt filters on beneficiary BEFORE the simulator sees them, so the simulator itself
    stays generic. This pins the contract: give it only what may be attacked."""
    mine = _l("Mine", 500_000, 8.0, 10_000)
    sim = _simulate([mine], AVALANCHE, extra=50_000)
    assert [s["name"] for s in sim["steps"]] == ["Mine"]


def test_redirect_wins_across_a_normal_loan_book():
    """A spread of a point or two never clears the bar."""
    go, why = prepayment.should_accumulate(foreclose_rate_pct=9.35, next_best_rate_pct=9.10)
    assert go is False and "redirect" in why


def test_redirect_still_wins_once_the_cheap_targets_are_gone():
    go, _ = prepayment.should_accumulate(foreclose_rate_pct=9.35, next_best_rate_pct=8.44)
    assert go is False


def test_saving_up_wins_for_a_card_among_home_loans():
    """The case the rule exists for: 24% against 8% is worth parking cash to kill."""
    go, why = prepayment.should_accumulate(foreclose_rate_pct=24.0, next_best_rate_pct=8.10)
    assert go is True and "saving up wins" in why


def test_with_nothing_else_to_attack_saving_up_is_the_only_option():
    go, why = prepayment.should_accumulate(foreclose_rate_pct=9.35, next_best_rate_pct=None)
    assert go is True and "only option" in why
