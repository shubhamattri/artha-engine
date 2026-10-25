from datetime import date

from models.household import CashflowLine, Loan
from models.recurring import RecurringInvestment
from models.tenant import Household
from models.transaction import Transaction


def _ctxn(hid, amount, d, category, txn_type, merchant="X"):
    return Transaction(household_id=hid, fingerprint=f"c-{amount}-{d}-{txn_type}-{merchant}", date=d,
                       description=merchant, merchant=merchant, source="statement", amount=amount,
                       direction="debit", category=category, txn_type=txn_type, bank="X",
                       card_type="x", card_last4="0000", stmt_month=d[:7])


def _setup(db):
    h = Household(name="H"); db.add(h); db.commit()
    db.add(Loan(household_id=h.id, name="Car Loan", lender="HDFC", holder="S", category="Car",
                original_principal=1_000_000, annual_rate_pct=10.0, tenure_months=60,
                disbursement_date=date(2026, 1, 1), emi_override=16500.0))
    db.add(CashflowLine(household_id=h.id, kind="fixed", label="House Rent", amount_inr=45000.0))
    db.add(RecurringInvestment(household_id=h.id, name="Nifty SIP", kind="sip",
                               as_of_date=date(2026, 1, 1), monthly_contribution=10000.0, opening_balance=0.0))
    db.commit()
    return h


def test_reconcile_paid_missing_extra(db_session):
    from services import commitments
    h = _setup(db_session)
    db_session.add_all([
        _ctxn(h.id, 16500.0, "2026-06-05", "Loan EMI", "EMI", "Example Car Loan"),        # EMI → paid
        _ctxn(h.id, 45000.0, "2026-06-01", "Rent / Housing", "SPEND", "Landlord Rentals"),  # rent → paid
        # SIP has NO matching debit this month → missing
        _ctxn(h.id, 3300.0, "2026-06-10", "Loan EMI", "EMI", "ICICI Loan"),             # committed but no commitment → extra
    ]); db_session.commit()
    r = commitments.reconcile(db_session, h.id, "2026-06")
    paid = {p["ref"] for p in r["paid"]}
    missing = {m["ref"] for m in r["missing"]}
    assert "loan:1" in paid and "cashflow:1" in paid
    assert "recurring:1" in missing
    assert len(r["extra"]) == 1 and r["extra"][0]["amount"] == 3300.0
    assert r["paid_total"] == 16500.0 + 45000.0


def test_variable_spend_not_matched_to_commitment(db_session):
    """A ₹48k everyday SPEND (a TV) must never be mistaken for the ₹48k rent commitment — only
    committed-type debits are eligible."""
    from services import commitments
    h = _setup(db_session)
    db_session.add(_ctxn(h.id, 45000.0, "2026-06-02", "Shopping", "SPEND", "Big TV"))  # variable, not committed
    db_session.commit()
    r = commitments.reconcile(db_session, h.id, "2026-06")
    assert "cashflow:1" in {m["ref"] for m in r["missing"]}   # rent still unpaid (TV didn't count)
    assert r["extra"] == []                                    # the TV isn't even a committed debit


def test_same_amount_transfer_never_matches_loan(db_session):
    """The poisoning bug: a ₹18,200 person-transfer must NOT match the ₹18,200 loan EMI (wrong kind),
    so it can never be learned as the loan's payee."""
    from services import commitments
    h = _setup(db_session)  # loan:1 EMI = 16500
    db_session.add(_ctxn(h.id, 16500.0, "2026-06-05", "Internal Transfer", "TRANSFER", "Rohit Nair"))
    db_session.commit()
    r = commitments.reconcile(db_session, h.id, "2026-06")
    assert "loan:1" in {m["ref"] for m in r["missing"]}          # EMI unmatched (no real EMI debit)
    assert all(p["ref"] != "loan:1" for p in r["paid"])          # the transfer did NOT match the loan


def test_person_payment_needs_confirmation_not_amount(db_session):
    """The one-off lawyer-fee case: a person-payment commitment must NOT match a coincidental same-amount
    transfer (a one-off ₹6,000 lawyer fee ≈ the ₹6,000 school line). It stays missing until the real
    payee is confirmed, then matches by identity."""
    from services import commitments, learned
    h = Household(name="HL"); db_session.add(h); db_session.commit()
    db_session.add(CashflowLine(household_id=h.id, kind="fixed", label="Child / schooling", amount_inr=6000.0))
    db_session.commit()  # cashflow:1, {TRANSFER}
    db_session.add(_ctxn(h.id, 6000.0, "2026-06-05", "Internal Transfer", "TRANSFER", "Sample Legal Services"))
    db_session.commit()
    r = commitments.reconcile(db_session, h.id, "2026-06")
    assert "cashflow:1" in {m["ref"] for m in r["missing"]}      # the lawyer did NOT get counted as school
    assert all(p["ref"] != "cashflow:1" for p in r["paid"])
    # confirm the real school payee → now it matches by identity
    commitments.link(db_session, h.id, learned.signature("Greenwood School"), "cashflow:1", "user")
    db_session.add(_ctxn(h.id, 6000.0, "2026-06-06", "Internal Transfer", "TRANSFER", "Greenwood School"))
    db_session.commit()
    r2 = commitments.reconcile(db_session, h.id, "2026-06")
    assert any(p["ref"] == "cashflow:1" and p["via"] == "identity" for p in r2["paid"])


def test_learned_identity_matches_off_amount(db_session):
    """A learned payee links a debit to its commitment regardless of amount — so a first EMI (different
    figure, same payee) still matches, instead of showing as missing + extra."""
    from services import commitments, learned
    h = _setup(db_session)  # loan:1 EMI = 16500
    commitments.link(db_session, h.id, learned.signature("Example Auto Loan Mandate"), "loan:1", "user")
    db_session.add(_ctxn(h.id, 9000.0, "2026-06-05", "Loan EMI", "EMI", "Example Auto Loan Mandate"))
    db_session.commit()
    paid = {p["ref"]: p for p in commitments.reconcile(db_session, h.id, "2026-06")["paid"]}
    assert "loan:1" in paid and paid["loan:1"]["via"] == "identity" and paid["loan:1"]["actual"] == 9000.0


def test_identity_distinguishes_same_amount_commitments(db_session):
    """The cook and the maid are both ₹7,000 — only the learned payee (Deepa vs Meera) can put the
    right debit against the right job. Amount alone can't."""
    from services import commitments, learned
    h = Household(name="HM"); db_session.add(h); db_session.commit()
    db_session.add_all([
        CashflowLine(household_id=h.id, kind="fixed", label="Cook", amount_inr=7000.0),        # cashflow:1
        CashflowLine(household_id=h.id, kind="fixed", label="House Maid", amount_inr=7000.0),   # cashflow:2
    ]); db_session.commit()
    commitments.link(db_session, h.id, learned.signature("Deepa"), "cashflow:1", "user")
    commitments.link(db_session, h.id, learned.signature("Meera"), "cashflow:2", "user")
    db_session.add_all([
        _ctxn(h.id, 7000.0, "2026-06-05", "Internal Transfer", "TRANSFER", "Deepa"),
        _ctxn(h.id, 7000.0, "2026-06-06", "Internal Transfer", "TRANSFER", "Meera"),
    ]); db_session.commit()
    paid = {p["ref"]: p["payee"] for p in commitments.reconcile(db_session, h.id, "2026-06")["paid"]}
    assert paid.get("cashflow:1") == "Deepa" and paid.get("cashflow:2") == "Meera"


def test_confirmed_link_recategorizes_out_of_variable_spend(db_session):
    """Regression: the maid's ₹7,000 mis-filed as 'Personal / Misc' (variable spend) must be pushed to
    its committed category (Internal Transfer) by her confirmed link — so it stops inflating the budget
    — while STILL matching the commitment. Link saved as short 'Meera' vs full bank payee also tested."""
    from models.transaction import Transaction
    from services import commitments, learned
    h = Household(name="HR"); db_session.add(h); db_session.commit()
    db_session.add(CashflowLine(household_id=h.id, kind="fixed", label="House Maid", amount_inr=7000.0))  # cashflow:1
    db_session.commit()
    commitments.link(db_session, h.id, learned.signature("Meera"), "cashflow:1", "user")
    db_session.add(_ctxn(h.id, 7000.0, "2026-06-05", "Personal / Misc", "SPEND", "Meera Sundaram Iyer"))
    db_session.commit()

    assert commitments.apply_link_categories(db_session, h.id)["recategorized"] == 1
    t = db_session.query(Transaction).filter(Transaction.household_id == h.id).first()
    assert t.category == "Internal Transfer" and t.txn_type == "TRANSFER" and t.confidence == "high"
    assert "cashflow:1" in {p["ref"] for p in commitments.reconcile(db_session, h.id, "2026-06")["paid"]}


def test_commitments_are_month_aware_by_start_date(db_session):
    """A loan/expense is only a commitment for the months it actually exists — a loan disbursed in June
    must NOT show as due (missing) in January, and a fixed expense started in June likewise."""
    from datetime import date
    from services import commitments
    h = Household(name="HMA"); db_session.add(h); db_session.commit()
    db_session.add_all([
        Loan(household_id=h.id, name="BMW Loan", lender="BMW", holder="Household", category="Car",
             original_principal=5000000, annual_rate_pct=7.65, tenure_months=84,
             disbursement_date=date(2026, 6, 1)),
        CashflowLine(household_id=h.id, kind="fixed", label="Ananya Pocket Money", amount_inr=25000.0,
                     start_date=date(2026, 6, 1)),
    ]); db_session.commit()

    jan = {c["name"] for c in commitments.list_commitments(db_session, h.id, "2026-01")}
    jul = {c["name"] for c in commitments.list_commitments(db_session, h.id, "2026-07")}
    assert "BMW Loan" not in jan and "Ananya Pocket Money" not in jan   # didn't exist yet in Jan
    assert "BMW Loan" in jul and "Ananya Pocket Money" in jul           # live by July


def test_candidates_offer_transfers_for_person_commitment(db_session):
    """The exception queue: Ananya's ₹25k pocket money is a person-transfer the system won't auto-match by
    amount, so `candidates` offers the month's transfers to pick from — closest amount first — and once
    linked it reconciles + a wrong link can be undone."""
    from services import commitments, learned
    h = Household(name="HC"); db_session.add(h); db_session.commit()
    db_session.add(CashflowLine(household_id=h.id, kind="fixed", label="Ananya Pocket Money",
                                amount_inr=25000.0))  # cashflow:1, {TRANSFER}
    db_session.commit()
    db_session.add_all([
        _ctxn(h.id, 25000.0, "2026-06-05", "Internal Transfer", "TRANSFER", "Vikram Rao"),   # the real one
        _ctxn(h.id, 4000.0, "2026-06-08", "Internal Transfer", "TRANSFER", "Nikhil Shah"),  # a distractor
        _ctxn(h.id, 9000.0, "2026-06-09", "Shopping", "SPEND", "Big Bazaar"),                  # wrong kind — excluded
    ]); db_session.commit()

    # missing until confirmed
    assert "cashflow:1" in {m["ref"] for m in commitments.reconcile(db_session, h.id, "2026-06")["missing"]}
    cand = commitments.candidates(db_session, h.id, "cashflow:1", "2026-06")["candidates"]
    payees = [c["payee"] for c in cand]
    assert payees[0] == "Vikram Rao"          # closest to ₹25k ranks first
    assert "Big Bazaar" not in payees          # a SPEND is not a person-transfer candidate

    # link the chosen payee → now it reconciles by identity
    commitments.link(db_session, h.id, learned.signature("Vikram Rao"), "cashflow:1", "user")
    assert any(p["ref"] == "cashflow:1" and p["via"] == "identity"
               for p in commitments.reconcile(db_session, h.id, "2026-06")["paid"])
    # undo → back to missing
    assert commitments.unlink(db_session, h.id, learned.signature("Vikram Rao")) is True
    assert "cashflow:1" in {m["ref"] for m in commitments.reconcile(db_session, h.id, "2026-06")["missing"]}


def test_three_identical_sips_match_one_to_one(db_session):
    """Three ₹10k SIPs must each claim a distinct ₹10k saving debit (1:1), not all fold onto one."""
    from services import commitments
    h = Household(name="H2"); db_session.add(h); db_session.commit()
    for i in range(3):
        db_session.add(RecurringInvestment(household_id=h.id, name=f"SIP{i}", kind="sip",
                                           as_of_date=date(2026, 1, 1), monthly_contribution=10000.0, opening_balance=0.0))
    db_session.commit()
    db_session.add_all([_ctxn(h.id, 10000.0, "2026-06-05", "Savings & Investments", "SAVING", f"Groww{i}") for i in range(2)])
    db_session.commit()
    r = commitments.reconcile(db_session, h.id, "2026-06")
    assert len(r["paid"]) == 2 and len(r["missing"]) == 1     # 2 debits → 2 SIPs paid, 1 missing
