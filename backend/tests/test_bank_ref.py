"""Extractor tests use synthetic SMS fixtures modelled character-for-character on real bank layouts.

The strings below are invented, but their SHAPE (field order, separators, mask patterns, digit counts)
mirrors what the banks actually send — that shape is what the extractor is pinned against.
"""

from datetime import datetime

from services.bank_ref import extract_ref, extract_txn_time

HDFC_CARD = "Spent Rs.1450 On HDFC Bank Card 4321 At EXAMPLETRIP(INDIA) P On 2026-07-01:15:51:20.Not You?"
HDFC_UPI = ("Sent Rs.2400.00\nFrom HDFC Bank A/C *7654\nTo Example Logistics Ltd\nOn 01/07/26\n"
            "Ref 400100200300\nNot You?")
HDFC_NEFT = "HDFC Bank : NEFT money transfer Txn No HDFCN00011122233 for Rs INR 7,500.00 has been credited"
AXIS_CARD = "Spent INR 210\nAxis Bank Card no. XX7788\n01-07-26 11:09:46 IST\nEXAMPLE CAF\nAvl Limit: INR 250000.00"
NO_REF = "Amt Deducted! Rs.10000 from your HDFC Bank A/c XX4455 for Money Transfer via HDFC Bank Online Banking."


def test_upi_ref_extracted():
    assert extract_ref(HDFC_UPI) == "400100200300"


def test_neft_txn_no_extracted():
    assert extract_ref(HDFC_NEFT) == "HDFCN00011122233"


def test_card_txn_has_no_ref():
    assert extract_ref(HDFC_CARD) is None
    assert extract_ref(AXIS_CARD) is None


def test_phone_numbers_not_mistaken_for_ref():
    # "Call 18001234567" / "SMS BLOCK CC 4321 to 7300000000" carry no Ref/RRN label → must not match
    assert extract_ref("Not You? Call 18001234567/SMS BLOCK CC 4321 to 7300000000") is None


def test_hdfc_card_timestamp():
    assert extract_txn_time(HDFC_CARD) == datetime(2026, 7, 1, 15, 51, 20)


def test_axis_card_timestamp():
    assert extract_txn_time(AXIS_CARD) == datetime(2026, 7, 1, 11, 9, 46)


def test_no_timestamp_when_absent():
    assert extract_txn_time(NO_REF) is None


def test_two_card_txns_same_time_are_one_debit():
    # identical printed time = one debit forwarded twice; different time = two debits
    a = "Spent Rs.500 On HDFC Bank Card 4321 At EXAMPLE CAFE On 2026-06-20:20:00:00"
    b = "Spent Rs.500 On HDFC Bank Card 4321 At EXAMPLE CAFE On 2026-06-20:20:00:00"
    c = "Spent Rs.500 On HDFC Bank Card 4321 At EXAMPLE CAFE On 2026-06-20:21:30:00"
    assert extract_txn_time(a) == extract_txn_time(b)
    assert extract_txn_time(a) != extract_txn_time(c)


# ── Unlabelled / positional refs, in the shapes seen in statements + SMS ──────────────

def test_imps_positional_ref():
    """IMPS-<RRN> is a real per-transfer reference; the IB-funds-transfer account number is not."""
    assert extract_ref("IMPS-500100200300-ASHA VERMA-UTIB-XXXXXXXXXXXX7788-IMPS TRANSACTION") == "500100200300"
    assert extract_ref("IB FUNDS TRANSFER DR-11122233344455-ASHA VERMA") is None   # account number, not a ref


def test_two_imps_transfers_get_different_rrns():
    """Two IMPS transfers to the same payee carry DIFFERENT RRNs → they never collide/merge."""
    a = extract_ref("IMPS-500100200300-ASHA VERMA-UTIB-XXXXXXXXXXXX7788-IMPS TRANSACTION")
    b = extract_ref("IMPS-500100200400-ASHA VERMA-UTIB-XXXXXXXXXXXX7788-IMPS TRANSACTION")
    assert a and b and a != b


def test_upi_rrn_anchored_not_vpa_phone():
    assert extract_ref("UPI-EXAMPLE LAUNDRY-GPAY-90000123450@OKBIZAXIS-UTIB0000123-110022003300-UPI") == "110022003300"
    # a VPA-embedded 12-digit handle with NO anchored RRN must yield None — never grab vyapar.100200300400
    assert extract_ref("UPI/EXAMPLE MOTORS/vyapar.100200300400@hdfcbank/9800011122@ptyes") is None


def test_card_network_leading_posting_ref_differs_per_order():
    a = extract_ref("10000000011 Example Foods Limited Bengaluru IN 6")
    b = extract_ref("10000000022 Example Foods Limited Bengaluru IN 6")
    assert a == "10000000011" and b == "10000000022" and a != b


def test_payu_id():
    assert extract_ref("A1B2C3D4E5F6G7H8I9/PAYUAXISBANK") == "A1B2C3D4E5F6G7H8I9"


def test_refless_narration_goes_to_review():
    assert extract_ref("UPI-ROHIT NAIR K") is None
    assert extract_ref("EXAMPLECLOUD EXAMPLECLOUD  USD12.34") is None


def test_ib_funds_transfer_account_number_not_extracted():
    """CRITICAL: 'IB FUNDS TRANSFER DR/CR-<n>' is the beneficiary ACCOUNT number, constant across every
    transfer to that payee — it must NOT be extracted as a ref (would merge all recurring transfers)."""
    assert extract_ref("IB FUNDS TRANSFER CR-11122233344455-ASHA VERMA") is None
    assert extract_ref("IB FUNDS TRANSFER DR-11122233344455-ASHA VERMA") is None


def test_backfill_poison_guard_drops_cross_date_refs(db_session):
    """A ref that appears across a >3-day span is a recurring identifier, not a per-txn ref — the guard
    must drop it so unrelated transactions never merge."""
    from models.tenant import Household
    from models.transaction import Transaction
    from services import bank_ref as br
    h = Household(name="P"); db_session.add(h); db_session.commit()
    # Same fake 'ref' (via a UPI RRN we force) on two dates 30 days apart → must be dropped.
    def mk(fp, d):
        return Transaction(household_id=h.id, fingerprint=fp, date=d, description="UPI-X-BANK-999888777666-UPI",
                           merchant="X", source="statement", amount=5000.0, direction="debit",
                           category="x", txn_type="SPEND", bank="HDFC", card_type="c", card_last4="1111",
                           stmt_month=d[:7])
    db_session.add_all([mk("p1", "2026-01-02"), mk("p2", "2026-02-01")]); db_session.commit()
    res = br.backfill(db_session, h.id, dry_run=True)
    assert res["filled"] == 0 and res["dropped_recurring"] == 2   # poisoned ref dropped, nothing written
