"""Each test here is a mistake that costs real money if the rule is wrong."""

from datetime import date

from services.capital_gains import compute_gain

SLAB = 0.312


def test_loan_repayment_does_not_reduce_the_gain():
    """A property sale: sold Rs 80L, part of which repaid the loan before the household saw any of it.
    The gain is computed on the full Rs 80L. Passing the loan in as a cost would understate tax."""
    r = compute_gain(asset_class="property", gross_proceeds=8_000_000, costs=0,
                     cost_basis=5_500_000, acquired_on=date(2022, 3, 1),
                     disposed_on=date(2025, 7, 1), slab_rate=SLAB)
    assert r.gain_inr == 2_500_000        # not the net proceeds, and not 80L minus what was repaid
    assert r.is_long_term is True


def test_property_before_cutoff_picks_the_cheaper_of_two_methods():
    plain = compute_gain(asset_class="property", gross_proceeds=8_000_000, costs=0,
                         cost_basis=5_500_000, acquired_on=date(2022, 3, 1),
                         disposed_on=date(2025, 7, 1), slab_rate=SLAB)
    indexed = compute_gain(asset_class="property", gross_proceeds=8_000_000, costs=0,
                           cost_basis=5_500_000, acquired_on=date(2022, 3, 1),
                           disposed_on=date(2025, 7, 1), slab_rate=SLAB,
                           cii_acquired=331, cii_disposed=390)
    assert plain.method == "ltcg"
    assert indexed.method == "ltcg_indexed"
    assert indexed.tax_inr < plain.tax_inr          # indexation wins on this one
    assert indexed.rate_used == 0.20


def test_foreign_share_needs_24_months_not_12():
    """A share listed only on a foreign exchange counts as UNLISTED for Indian tax. An 18-month hold that would be
    long-term for an Indian share is short-term here, at slab instead of 12.5%."""
    r = compute_gain(asset_class="equity_foreign", gross_proceeds=1_000_000, costs=0,
                     cost_basis=600_000, acquired_on=date(2024, 1, 1),
                     disposed_on=date(2025, 7, 1), slab_rate=SLAB)
    assert r.is_long_term is False
    assert r.rate_used == SLAB
    ind = compute_gain(asset_class="equity_listed_in", gross_proceeds=1_000_000, costs=0,
                       cost_basis=600_000, acquired_on=date(2024, 1, 1),
                       disposed_on=date(2025, 7, 1), slab_rate=SLAB)
    assert ind.is_long_term is True                  # same dates, Indian listed → long-term
    assert ind.tax_inr < r.tax_inr


def test_only_indian_listed_equity_gets_the_exemption():
    kw = dict(gross_proceeds=1_000_000, costs=0, cost_basis=600_000,
              acquired_on=date(2022, 1, 1), disposed_on=date(2025, 7, 1), slab_rate=SLAB)
    ind = compute_gain(asset_class="equity_listed_in", **kw)
    foreign = compute_gain(asset_class="equity_foreign", **kw)
    assert ind.tax_inr < foreign.tax_inr             # Rs 1.25L exemption applies only to the first
    assert round(foreign.tax_inr, 0) == round(400_000 * 0.125 * 1.04, 0)


def test_unknown_cost_basis_returns_none_not_zero():
    """ESOP lots whose FMV-at-exercise is not to hand. Treating a missing basis as zero would
    invent a gain equal to the entire sale value."""
    r = compute_gain(asset_class="equity_foreign", gross_proceeds=2_500_000, costs=0,
                     cost_basis=None, acquired_on=None, disposed_on=date(2026, 6, 1), slab_rate=SLAB)
    assert r.gain_inr is None and r.tax_inr is None
    assert "FMV" in r.why


def test_debt_fund_is_slab_regardless_of_holding_period():
    r = compute_gain(asset_class="debt_fund", gross_proceeds=1_000_000, costs=0,
                     cost_basis=800_000, acquired_on=date(2023, 5, 1),
                     disposed_on=date(2026, 6, 1), slab_rate=SLAB)
    assert r.is_long_term is False and r.rate_used == SLAB
