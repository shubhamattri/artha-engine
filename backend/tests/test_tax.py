"""The tax engine exists to change the payoff ORDER. These assert it actually does."""

from services.tax import (
    REGIME_NEW,
    REGIME_OLD,
    marginal_rate,
    effective_rates,
    payoff_order,
)

INCOMES = {"Asha Verma": 3_900_000.0, "Rohit Nair": 2_600_000.0, "Parent": 0.0}


def _loans():
    return [
        {"name": "Education", "balance_inr": 1_000_000, "rate_pct": 9.50,
         "category": "Education", "holder": "Asha Verma"},
        {"name": "Car",       "balance_inr": 800_000,   "rate_pct": 9.35,
         "category": "Car",       "holder": "Rohit Nair"},
        {"name": "Gold",      "balance_inr": 950_000,   "rate_pct": 9.10,
         "category": "Gold Loan", "holder": "Parent"},
        {"name": "HomeBig",   "balance_inr": 2_800_000, "rate_pct": 8.10,
         "category": "Home",      "holder": "Asha Verma"},
        {"name": "HomeSmall", "balance_inr": 900_000,   "rate_pct": 8.45,
         "category": "Home",      "holder": "Asha Verma"},
    ]


def test_marginal_rate_includes_surcharge_and_cess():
    assert marginal_rate(0, REGIME_OLD) == 0.0                  # no income, no relief
    assert round(marginal_rate(3_900_000, REGIME_OLD), 4) == 0.312   # 30% + 4% cess, no surcharge
    over_50l = marginal_rate(6_000_000, REGIME_OLD)             # 10% surcharge kicks in
    assert round(over_50l, 4) == round(0.30 * 1.10 * 1.04, 4)
    assert marginal_rate(6_000_000, REGIME_OLD) > marginal_rate(3_900_000, REGIME_OLD)


def test_new_regime_shelters_nothing():
    """The whole point: on the new regime the nominal ranking is already right."""
    for t in effective_rates(_loans(), INCOMES, REGIME_NEW):
        assert t.effective_rate_pct == t.nominal_rate_pct
        assert t.shielded_fraction == 0.0


def test_80e_makes_the_priciest_loan_the_cheapest():
    """9.50% education beats 9.10% gold on paper, yet costs less after 80E. This is the bug."""
    by = {t.name: t for t in effective_rates(_loans(), INCOMES, REGIME_OLD)}
    edu, gold = by["Education"], by["Gold"]
    assert edu.nominal_rate_pct > gold.nominal_rate_pct          # nominal says education first
    assert edu.effective_rate_pct < gold.effective_rate_pct      # reality says gold first
    assert round(edu.effective_rate_pct, 2) == round(9.50 * (1 - 0.312), 2)
    assert edu.section == "80E"


def test_24b_cap_full_means_no_marginal_relief():
    """Home interest already exceeds the ₹2L cap, so prepaying saves no tax and the real rate is
    the nominal rate. Treating all home interest as deductible understates every loan past the cap."""
    by = {t.name: t for t in effective_rates(_loans(), INCOMES, REGIME_OLD)}
    assert by["HomeBig"].shielded_fraction == 0.0
    assert by["HomeBig"].effective_rate_pct == by["HomeBig"].nominal_rate_pct
    assert "cap already full" in by["HomeBig"].why


def test_24b_within_cap_does_shelter():
    small = [{"name": "OnlyHome", "balance_inr": 1_500_000, "rate_pct": 8.0,
              "category": "Home", "holder": "Asha Verma"}]           # ₹1.2L interest, under the cap
    t = effective_rates(small, INCOMES, REGIME_OLD)[0]
    assert t.shielded_fraction == 1.0
    assert t.effective_rate_pct < t.nominal_rate_pct


def test_let_out_property_escapes_the_cap():
    big = [{"name": "LetOut", "balance_inr": 2_800_000, "rate_pct": 8.10,
            "category": "Home", "holder": "Asha Verma"}]
    capped = effective_rates(big, INCOMES, REGIME_OLD)[0]
    let = effective_rates(big, INCOMES, REGIME_OLD, let_out_holders={"Asha Verma"})[0]
    assert capped.shielded_fraction == 0.0
    assert let.shielded_fraction == 1.0
    assert let.effective_rate_pct < capped.effective_rate_pct


def test_deduction_is_worthless_to_a_non_earner():
    """The parent holds the gold loan and has no taxable income — no section would help her anyway, but
    the same must hold for a deductible category."""
    edu_mom = [{"name": "E", "balance_inr": 500_000, "rate_pct": 9.5,
                "category": "Education", "holder": "Parent"}]
    t = effective_rates(edu_mom, INCOMES, REGIME_OLD)[0]
    assert t.effective_rate_pct == t.nominal_rate_pct
    assert "no taxable income" in t.why


def test_payoff_order_flips_between_regimes():
    new = [l["name"] for l in payoff_order(_loans(), INCOMES, REGIME_NEW)]
    old = [l["name"] for l in payoff_order(_loans(), INCOMES, REGIME_OLD)]
    assert new[0] == "Education"        # nominal: 9.50% looks like the worst
    assert old[0] == "Car"              # real: 9.35% unshielded is the worst
    assert old.index("Education") > old.index("Gold")
    assert new != old                   # the regime genuinely changes what you should do first
