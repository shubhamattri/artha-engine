"""A card purchase at a merchant must never be classified as a loan EMI.

The rule listed lenders as "Kotak/Bajaj/HDFC/ICICI Loan", and every ICICI card alert contains the
word ICICI because that is who ISSUED the card. A card purchase was tagged Loan EMI, which
excluded it from variable spend, so it looked like a capture failure rather than a misclassification.
"""

def _rules() -> str:
    import services.ai_categorize as m
    for name in dir(m):
        v = getattr(m, name)
        if isinstance(v, str) and "Loan / EMI repayment" in v:
            return v
    raise AssertionError("could not find the classification rules")


def test_the_rule_says_the_lender_must_be_the_payee():
    """Without this, the issuing bank's name alone is enough to call a purchase an EMI."""
    r = _rules()
    assert "LENDER must be who is being PAID" in r
    assert "issued the card is not the lender" in r


def test_the_rule_names_the_exact_case_that_broke():
    """A concrete counter-example is what stops the model generalising the bank name again."""
    r = _rules()
    assert "AMAZON PAY" in r and "never a loan emi" in r.lower()
