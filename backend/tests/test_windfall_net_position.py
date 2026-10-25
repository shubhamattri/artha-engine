"""Both arms have to be measured the same way, or the comparison decides the answer by itself.

The calculator showed the invest arm as ten years of compounding on the principal, and the prepay arm
as interest avoided and nothing else. The EMI that prepaying frees was counted NOWHERE, and neither
were the months at the end where one arm has stopped paying and the other has not.

That made prepaying look like it lost by a wide margin. Accounted the same way
it may win. `comparable: false` does not rescue it: putting one figure beside the other and captioning it
"not the same kind of number" still lets the reader draw the conclusion, so the presentation was worse
than showing nothing.

The honest comparison is the household's invested position at the SAME horizon under both paths, at
the SAME rate, with every rupee of freed cash flow reinvested in both. It still does not pick a
winner, and it must not: the answer flips with the rate, which is the whole point of showing a range.
"""

import pytest


def _fv_ann(pmt: float, r: float, n: int) -> float:
    v = 0.0
    for _ in range(int(n)):
        v = (v + pmt) * (1 + r)
    return v


def _args(**over):
    a = {"amount": 2_000_000.0, "emi": 160_000.0, "surplus": 100_000.0,
         "months_baseline": 54, "months_with_lump": 43, "emi_freed": 25_000.0,
         "horizon_months": 120, "rate_pct": 9.0}
    a.update(over)
    return a


def test_the_invest_arm_carries_the_freed_cashflow_after_the_debt_clears(monkeypatch):
    """Arm A is not just the lump growing. Once the debt clears the whole EMI plus surplus is free and
    goes to work — leaving it out understates investing exactly as badly as the old code understated
    prepaying."""
    from api.windfall import net_position
    a = _args()
    out = net_position(**a)
    r = a["rate_pct"] / 100 / 12
    lump_only = a["amount"] * (1 + r) ** a["horizon_months"]
    assert out["invest"]["value_inr"] > lump_only + 1.0


def test_the_prepay_arm_counts_the_emi_the_lump_freed(monkeypatch):
    """The defect, stated as a test. Freeing Rs 25,000/mo for 43 months is real money."""
    from api.windfall import net_position
    with_freed = net_position(**_args())["prepay"]["value_inr"]
    without = net_position(**_args(emi_freed=0.0))["prepay"]["value_inr"]
    assert with_freed > without + 1.0


def test_the_prepay_arm_counts_the_months_the_other_arm_is_still_paying(monkeypatch):
    """Clearing 11 months earlier means 11 months where one arm invests EMI+surplus and the other pays
    it to a lender. That gap is most of the prepay case and was absent entirely."""
    from api.windfall import net_position
    early = net_position(**_args(months_with_lump=43))["prepay"]["value_inr"]
    late = net_position(**_args(months_with_lump=54))["prepay"]["value_inr"]
    assert early > late + 1.0


def test_both_arms_use_the_same_rate_and_the_same_horizon(monkeypatch):
    """Two rates, or two horizons, and the comparison is measuring the inputs rather than the paths."""
    from api.windfall import net_position
    out = net_position(**_args())
    assert out["rate_pct"] == 9.0
    assert out["horizon_months"] == 120
    assert out["invest"]["horizon_months"] == out["prepay"]["horizon_months"] == 120


def test_it_reports_the_difference_without_naming_a_winner(monkeypatch):
    """It may state which is larger as arithmetic. It may not rank, score, recommend or advise."""
    import json

    from api.windfall import net_position
    out = net_position(**_args())
    blob = json.dumps(out).lower()
    for banned in ("recommend", "better", "best", "winner", "should", "advice", "verdict", "prefer"):
        assert banned not in blob, f"{banned!r} turns a calculator into advice"
    assert "difference_inr" in out


def test_a_high_enough_return_flips_the_answer(monkeypatch):
    """The reason no number may be published as the answer. Prepaying earns a contracted rate; above
    some return investing wins, and where that crossover sits is what the household is really deciding."""
    from api.windfall import net_position
    low = net_position(**_args(rate_pct=6.0))
    high = net_position(**_args(rate_pct=40.0))
    assert low["prepay"]["value_inr"] > low["invest"]["value_inr"]
    assert high["invest"]["value_inr"] > high["prepay"]["value_inr"]


def test_a_horizon_shorter_than_the_payoff_never_goes_negative(monkeypatch):
    """A 12-month horizon against a 54-month payoff has no post-clearance months at all. The annuity
    must floor at zero rather than compounding a negative count into nonsense."""
    from api.windfall import net_position
    out = net_position(**_args(horizon_months=12))
    assert out["invest"]["value_inr"] > 0
    assert out["prepay"]["value_inr"] >= 0


def test_the_arithmetic_matches_a_hand_rolled_annuity(monkeypatch):
    """Pins the actual formula, so a later refactor cannot quietly change what is being compared."""
    from api.windfall import net_position
    a = _args()
    r = a["rate_pct"] / 100 / 12
    out = net_position(**a)
    expect_invest = (a["amount"] * (1 + r) ** a["horizon_months"]
                     + _fv_ann(a["emi"] + a["surplus"], r,
                               a["horizon_months"] - a["months_baseline"]))
    assert out["invest"]["value_inr"] == pytest.approx(expect_invest, rel=1e-6)
