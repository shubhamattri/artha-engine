"""A card EMI conversion is an instalment of a PURCHASE, not a repayment on a loan.

Indian issuers rewrite a converted card purchase as "EMI <MERCHANT><CITY>". The classifier read that
leading "EMI " as the signal and called all of them Loan EMI / txn_type EMI, so a Google Workspace
subscription and an Anthropic API bill were counted as debt servicing and dropped out of spend
entirely. The household's real loan EMI is owned by the loan module; nothing in the ledger should be
adding to it.

Two different facts, and the classifier must keep them apart:
  • paid in instalments  → HOW it is paid. Still spend, in the category of whatever was bought.
  • repayment on a loan  → WHO is paid. Debt servicing, txn_type EMI.

The merchant behind the prefix is the only thing that can tell them apart, so that is what gets
classified. When the remainder names nobody the rules know, the prefix is left to decide — a bare
loan-account narration ("EMI 455708020 CHQ S...") stays a loan repayment, and an unrecognised payee
is never silently moved into spend.
"""

from __future__ import annotations

import importlib
import json

import pytest
from parsers.categorizer import classify, instalment_of

# The five rows that exposed the bug, verbatim from the household's July 2026 statements.
LIVE_EXAMPLES = {
    "EMI GOOGLE WORKSPACEMUMBAI": ("Entertainment & Subscriptions", "SPEND"),
    "EMI ANTHROPIC* CLAUDE TEAMANTHROPIC. USD 885.00": ("Entertainment & Subscriptions", "SPEND"),
    "EMI POLICYBAZAAR COMGURGAON": ("Insurance", "SPEND"),
    "EMI INDIGO AINEGURGAON": ("Transport", "SPEND"),
    # Unrecognised payee: the classifier cannot tell a vehicle dealer from a vehicle lender, so it
    # does NOT guess. Left where it was rather than silently moved into spend.
    "EMI PATHANKOT VEHICLEADESGURDASPUR": ("Loan EMI", "EMI"),
}


@pytest.mark.parametrize(("description", "expected"), sorted(LIVE_EXAMPLES.items()))
def test_the_five_live_rows(description: str, expected: tuple[str, str]) -> None:
    category, _conf, txn_type = classify(description, "debit")
    assert (category, txn_type) == expected


def test_a_subscription_billed_in_parts_is_not_debt_servicing() -> None:
    """The headline harm: anything reading txn_type EMI as debt was inflated by ordinary spend."""
    for d in ("EMI GOOGLE WORKSPACEMUMBAI",
              "EMI ANTHROPIC* CLAUDE TEAMANTHROPIC. USD 885.00",
              "EMI RAZ*SWIGGYBengaluru",
              "EMI BLINKITGURGAON"):
        _cat, _conf, txn_type = classify(d, "debit")
        assert txn_type != "EMI", f"{d!r} is still counted as debt servicing"


def test_real_loan_repayments_are_untouched() -> None:
    """Verbatim loan narrations from the same ledger. The fix must not cost the classifier these."""
    for d in ("EMI 455708020 CHQ S4557080200371 1225455708020",
              "EMI 151248946 CHQ S151248946120 1225151248946",
              "EMI 4000170463 CHQ S49176774000170463",
              "EMI 163211593",
              "BIL/Home Loan XX12345 EMI Member",
              "BIL/Personal Loan XX11223 EMI Memb",
              "MER EMI ,INT NBR:01,0 0000136043039 (Ref# 09999999980221000243041)",
              "EMI Interest - 1/6, Ref# 68030165",
              "EMI Principal - 1/6, Ref# 68030165",
              "455708020-EMI RTN CHARGES-MARCH-202601/0-EPR2607564453106"):
        category, _conf, txn_type = classify(d, "debit")
        assert (category, txn_type) == ("Loan EMI", "EMI"), f"{d!r} -> {category}/{txn_type}"


def test_a_narration_ENDING_in_emi_is_left_exactly_where_it_was() -> None:
    """A separate, pre-existing gap, pinned so this fix cannot be blamed for it and so a later fix
    is visible: the "emi " token needs a trailing space, so a narration that ENDS in EMI misses the
    loan rule and falls to the bucket. Production gets these right one layer up, from the learned
    store — the keyword rules alone do not."""
    for d in ("Kotak Bank Loan EMI", "HDFC Bank EMI", "BIL/Education Loan XX33445 Pre EMI"):
        assert classify(d, "debit")[::2] == ("Personal / Misc", "SPEND"), d


def test_a_lender_behind_the_prefix_is_still_a_loan() -> None:
    """The remainder decides, and a remainder naming a loan is a loan. Not a prefix rule in reverse."""
    for d in ("EMI HOME LOAN 40234522 HDFC", "EMI-PAYMENT TOWARDS LOAN 991"):
        category, _conf, txn_type = classify(d, "debit")
        assert (category, txn_type) == ("Loan EMI", "EMI"), f"{d!r} -> {category}/{txn_type}"


def test_instalment_of_names_the_purchase_not_the_payment_method() -> None:
    """The two ideas are separable on their own, so a caller can say 'paid in parts' without
    saying 'debt'."""
    assert instalment_of("EMI GOOGLE WORKSPACEMUMBAI") == "GOOGLE WORKSPACEMUMBAI"
    assert instalment_of("EMI-BLINKITGURGAON") == "BLINKITGURGAON"
    assert instalment_of("  EMI POLICYBAZAAR COMGURGAON") == "POLICYBAZAAR COMGURGAON"
    # only a LEADING marker: "emi" inside a narration is a word, not a conversion.
    assert instalment_of("MER EMI ,INT NBR:01,0 0000136043039") is None
    assert instalment_of("BIL/Home Loan XX12345 EMI Member") is None
    assert instalment_of("Transaction conversion into EMI") is None
    # an employer or merchant whose name merely ends in -emi must not be read as a prefix
    assert instalment_of("SALEMI TRADERS BENGALURU") is None
    assert instalment_of("EMI") is None
    assert instalment_of("") is None
    assert instalment_of(None) is None


def test_credits_never_become_spend() -> None:
    """A reversal of a converted instalment is money coming back, whatever the merchant."""
    _cat, _conf, txn_type = classify("EMI GOOGLE WORKSPACEMUMBAI", "credit")
    assert txn_type == "REFUND"


def _reloaded(monkeypatch, path):
    """parsers.categorizer rebuilt against a specific household rules file."""
    import parsers.categorizer as m
    monkeypatch.setenv("ARTHA_HOUSEHOLD_RULES", str(path))
    return importlib.reload(m)


def test_household_lender_token_still_reaches_the_instalment_path(monkeypatch, tmp_path):
    """A household's own lender is data, not code. It must work behind the prefix too, and adding
    one must not disturb rule order — the category of the FIRST matching rule still wins."""
    import parsers.categorizer as original
    rules = tmp_path / "household_rules.json"
    rules.write_text(json.dumps({"Loan EMI": ["somebuilderhousing"],
                                 "Groceries": ["peoples mini"]}), encoding="utf-8")
    try:
        m = _reloaded(monkeypatch, rules)
        assert m.classify("EMI SOMEBUILDERHOUSING FINANCE", "debit")[::2] == ("Loan EMI", "EMI")
        assert m.classify("EMI PEOPLES MINI MARTBANGALORE", "debit")[::2] == ("Groceries", "SPEND")
        # order intact: the built-in Insurance rule still outranks the generic spend rules
        assert m.categorize("POLICYBAZAAR COMGURGAON")[0] == "Insurance"
    finally:
        monkeypatch.delenv("ARTHA_HOUSEHOLD_RULES", raising=False)
        importlib.reload(original)


def test_missing_household_file_leaves_the_built_in_rules_alone(monkeypatch, tmp_path):
    import parsers.categorizer as original
    try:
        m = _reloaded(monkeypatch, tmp_path / "nope.json")
        assert m.classify("EMI GOOGLE WORKSPACEMUMBAI", "debit")[::2] == (
            "Entertainment & Subscriptions", "SPEND")
        assert m.classify("EMI PATHANKOT VEHICLEADESGURDASPUR", "debit")[::2] == ("Loan EMI", "EMI")
    finally:
        monkeypatch.delenv("ARTHA_HOUSEHOLD_RULES", raising=False)
        importlib.reload(original)


def _rules_text() -> str:
    import services.ai_categorize as m
    for name in dir(m):
        v = getattr(m, name)
        if isinstance(v, str) and "Loan / EMI repayment" in v:
            return v
    raise AssertionError("could not find the classification rules")


def test_the_ai_prompt_separates_an_instalment_from_a_loan() -> None:
    """The AI sees the same narrations and made the same mistake for the same reason."""
    r = _rules_text().lower()
    assert "emi " in r and "instal" in r
    assert "google workspace" in r          # the concrete counter-example that stops it generalising
    assert "loan account" in r or "lender" in r
