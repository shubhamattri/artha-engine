"""Canonical month-state: 'Coming up' must be reconciler-driven (only UNPAID commitments), not
amount-guessing. Once a commitment is matched to a real debit it drops off automatically."""

from services.month_state import coming_up_from_reconcile


def test_coming_up_lists_only_unpaid_commitments_grouped():
    rec = {"missing": [
        {"name": "HDFC CC", "amount": 8000, "kind": "EMI"},
        {"name": "Kotak", "amount": 7900, "kind": "EMI"},
        {"name": "Groww SIP", "amount": 5000, "kind": "SIP"},
        {"name": "Rent", "amount": 45000, "kind": "Fixed"},
        {"name": "Term cover", "amount": 2500, "kind": "Insurance"},
    ], "missing_total": 68400}
    out = coming_up_from_reconcile(rec)
    by_label = {g["label"]: g for g in out}
    assert by_label["Loan EMIs"]["count"] == 2 and by_label["Loan EMIs"]["amount"] == 15900
    assert by_label["SIPs & savings"]["count"] == 1
    assert by_label["Rent & household"]["amount"] == 45000
    assert "Insurance" in by_label


def test_all_paid_means_nothing_coming_up():
    assert coming_up_from_reconcile({"missing": [], "missing_total": 0}) == []
