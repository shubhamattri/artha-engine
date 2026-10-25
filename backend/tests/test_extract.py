"""Extraction is tested against synthetic alert fixtures modelled on real wording.

Every parsing bug this pipeline has had lived in wording nobody would have thought to invent: a
three-digit mask, a closing balance that reads like an amount, an account number interleaved with x's.
So every string below reproduces the exact wording and shape of a real alert, with all account
numbers, references, names and amounts replaced by invented ones.
"""

from datetime import date

from services.extract import extract


def test_the_alert_that_created_a_phantom_account():
    """The one that started it. Thirty transactions were filed under an account that does not exist
    because a three-digit mask was normalised into a phantom four-digit last4 at parse time."""
    f = extract("ICICI Bank Acc XX456 debited Rs. 5,000.00 on 05-Aug-26 InfoBIL*Education."
                "Avl Bal Rs. 41,000.50.To dispute call 18002662 or SMS BLOCK 456 to 9200000000")
    assert f.amount == 5000.00
    assert f.direction == "debit"
    assert f.occurred_on == date(2026, 8, 5)
    assert f.account_mask == "XX456", "the mask is kept verbatim, not turned into a fake last4"
    assert f.balance_after == 41000.50
    assert f.narration == "Education"
    assert f.confidence == "high"


def test_a_closing_balance_is_not_the_amount():
    """Avl Bal appears AFTER the amount and is larger. Reading it as the payment would overstate
    spending by the size of the account."""
    f = extract("ICICI Bank Acc XX456 debited Rs. 1,23,456.78 on 30-Jul-26 InfoEXMPLXX10203 ."
                "Avl Bal Rs. 12,345.67.")
    assert f.amount == 123456.78, "Indian digit grouping, and the amount not the balance"
    assert f.balance_after == 12345.67


def test_hdfc_upi_debit_names_the_account_in_prose():
    f = extract("Dear Customer, Greetings from HDFC Bank! Rs.5000.00 is debited from your account "
                "ending 5566 towards VPA 9000000000@pthdfc (SOME PAYEE NAME) on 01-08-26. "
                "UPI transaction reference no.: 500100200300")
    assert f.amount == 5000.0
    assert f.direction == "debit"
    assert f.account_mask == "5566"
    assert f.counterparty == "SOME PAYEE NAME", "the parenthesised payee beats the raw VPA"
    assert f.reference == "500100200300"
    assert f.occurred_on == date(2026, 8, 1)


def test_a_credit_is_not_a_debit():
    f = extract("Rs.65000 Credited to A/c ...4433 thru NEFT UTR HDFCH00011122233 by SOME SENDER. "
                "Total Bal:Rs.88000CR.")
    assert f.direction == "credit"
    assert f.amount == 65000.0
    assert f.account_mask == "4433"
    assert f.reference == "HDFCH00011122233"


def test_an_emi_reminder_is_flagged_as_due_not_paid():
    """A reminder is not a payment. Booking it as spend was a real bug class."""
    f = extract("EMI of Rs 25000 for Example Bank Home Loan XX1122 is due on 05-Aug-26. Please "
                "maintain sufficient funds in your linked Account XX3344 to avoid penal charges")
    assert f.kind_hint == "emi_due"
    assert f.amount == 25000.0


def test_an_otp_is_recognised_and_never_looks_like_a_transaction():
    f = extract("123456 is the OTP to verify your mobile number. Do not share it with anyone.")
    assert f.kind_hint == "otp"
    assert f.direction is None


def test_a_card_number_interleaved_with_masking_is_kept_whole():
    f = extract("Payment of INR 12,345.50 has been received on your ICICI Bank Credit Card "
                "Account 4xxx7788 on 18-JUN-26.")
    assert f.account_mask == "4xxx7788"
    assert f.amount == 12345.50
    assert f.kind_hint == "bill_paid"


def test_axis_card_spend():
    f = extract("Spent INR 68 Axis Bank Card no. XX7788 02-08-26 16:33:44 IST EXAMPLE ST "
                "Avl Limit: INR 250000")
    assert f.amount == 68.0
    assert f.direction == "debit"
    assert f.account_mask == "XX7788"


def test_absent_fields_stay_none_rather_than_becoming_defaults():
    """The whole premise. A fabricated value is worse than a missing one, because every later stage
    believes it — "0000" was treated as a real card for months."""
    f = extract("Your request to set UPI PIN on UPI app has failed.")
    assert f.amount is None
    assert f.account_mask is None
    assert f.direction is None
    assert f.occurred_on is None
    assert f.confidence == "low"


def test_empty_and_none_are_safe():
    for bad in (None, "", "   "):
        f = extract(bad)
        assert f.amount is None and f.confidence == "none"


def test_two_digit_years_resolve_to_this_century():
    assert extract("debited Rs 10 on 02-08-26").occurred_on == date(2026, 8, 2)
    assert extract("debited Rs 10 on 2026-08-02").occurred_on == date(2026, 8, 2)
    assert extract("debited Rs 10 on 02-Aug-26").occurred_on == date(2026, 8, 2)


def test_facts_serialise_for_storage():
    f = extract("ICICI Bank Acc XX456 debited Rs. 5,000.00 on 05-Aug-26")
    d = f.as_dict()
    assert d["occurred_on"] == "2026-08-05"
    assert d["account_mask"] == "XX456"
