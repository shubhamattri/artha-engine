"""Budget-learning engine: median-of-6-clean-months targets, one-off exclusion, band, and the
new-category / move-to-fixed detectors. Deterministic — no LLM in these assertions."""

from datetime import date

from models.household import Budget
from models.tenant import Household
from models.transaction import Transaction
from services.recommendations import _shift, recommend


def _stxn(hid, amount, month, category, merchant="Shop"):
    d = f"{month}-15"
    return Transaction(household_id=hid, fingerprint=f"r-{amount}-{d}-{category}-{merchant}", date=d,
                       description=merchant, merchant=merchant, source="statement", amount=amount,
                       direction="debit", category=category, txn_type="SPEND", bank="HDFC",
                       card_type="credit", card_last4="1111", stmt_month=month)


def test_median_of_clean_months_and_signals(db_session):
    h = Household(name="H"); db_session.add(h); db_session.commit()
    cur = date.today().strftime("%Y-%m")
    m = lambda k: _shift(cur, k)   # k months back
    groc = [28100, 31400, 29800, 30200, 32900, 30000]   # months -6..-1 → median 30100, band 28.1k–32.9k
    txns = [_stxn(h.id, amt, m(-6 + i), "Groceries") for i, amt in enumerate(groc)]
    txns.append(_stxn(h.id, 44000.0, m(-7), "Groceries"))   # a one-off month → EXCLUDED
    txns.append(_stxn(h.id, 9999.0, m(0), "Groceries"))     # current partial month → EXCLUDED
    for month in (m(-6), m(-5), m(-4), m(-3), m(-2), m(-1)):
        txns.append(_stxn(h.id, 4000.0, month, "Bills & Utilities"))   # dead-constant → move_to_fixed
    txns.append(_stxn(h.id, 5000.0, m(-1), "Education"))     # brand-new category, no target
    db_session.add_all(txns)
    db_session.add(Budget(household_id=h.id, category="Groceries", monthly_limit_inr=22000.0, month=m(-1)))
    db_session.commit()

    out = recommend(db_session, h.id, cycle_mode="calendar_month")
    g = next(r for r in out["recommendations"] if r["category"] == "Groceries")
    assert g["suggested"] == 30100.0                       # median of the 6 clean months
    assert g["band"] == [28100.0, 32900.0]                 # min–max of the clean window
    assert g["months_used"] == 6                           # one-off + current month excluded
    assert g["signal"] == "unrealistic"                    # ₹22k target sits below the band
    assert g["current_target"] == 22000.0

    assert "Education" in {c["category"] for c in out["new_categories"]}   # detected as new
    assert "Bills & Utilities" in {c["category"] for c in out["move_to_fixed"]}  # near-constant → fixed


def test_spike_month_with_two_midsize_oneoffs_is_excluded(db_session):
    """The v1-edge fix: a month with two ₹30k one-offs (neither ≥ ₹40k on its own, so a single-txn test
    misses it) sums to ₹60k of big-ticket spend and must be dropped — it can't skew the median or band."""
    h = Household(name="HS"); db_session.add(h); db_session.commit()
    cur = date.today().strftime("%Y-%m")
    m = lambda k: _shift(cur, k)
    txns = []
    for i, month in enumerate([m(-6), m(-5), m(-4), m(-2), m(-1)]):
        txns.append(_stxn(h.id, 10000.0, month, "Travel", f"trip{i}"))   # five clean ₹10k months
    txns.append(_stxn(h.id, 30000.0, m(-3), "Travel", "flightA"))        # spike month: two ₹30k …
    txns.append(_stxn(h.id, 30000.0, m(-3), "Travel", "flightB"))        # … = ₹60k big-ticket, excluded
    db_session.add_all(txns); db_session.commit()

    t = next(r for r in recommend(db_session, h.id, cycle_mode="calendar_month")["recommendations"] if r["category"] == "Travel")
    assert t["months_used"] == 5                # the spike month dropped, not counted
    assert t["suggested"] == 10000.0            # median of the five clean ₹10k months
    assert t["band"] == [10000.0, 10000.0]      # ₹60k never enters the band


def test_needs_two_clean_months_to_recommend(db_session):
    h = Household(name="H2"); db_session.add(h); db_session.commit()
    cur = date.today().strftime("%Y-%m")
    db_session.add(_stxn(h.id, 5000.0, _shift(cur, -1), "Travel"))   # only one clean month
    db_session.commit()
    out = recommend(db_session, h.id, cycle_mode="calendar_month")
    assert all(r["category"] != "Travel" for r in out["recommendations"])   # too little history
