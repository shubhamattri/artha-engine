"""Accounts/cards as user-managed data.

The point of this module is that onboarding a card must NOT require a developer. These tests pin the
behaviour that makes that true, including tenant isolation — accounts carry account numbers, so a leak
across households is the worst failure this table can have.
"""

from models.account import Account
from models.tenant import Household


def _hh(db, name="H1"):
    h = Household(name=name)
    db.add(h)
    db.commit()
    return h


def test_add_a_card_without_touching_code(db_session, client_for):
    h = _hh(db_session)
    c = client_for(h)
    r = c.post("/api/accounts", json={"bank": "Some Bank", "kind": "card", "last4": "6677",
                                      "holder": "A", "sms_senders": "SOMEBANKCRD"})
    assert r.status_code == 200, r.text
    assert r.json()["last4"] == "6677"
    assert [a["last4"] for a in c.get("/api/accounts").json()["accounts"]] == ["6677"]


def test_last4_is_validated_and_normalised(db_session, client_for):
    h = _hh(db_session)
    c = client_for(h)
    # a full card number must never be stored — only the last 4 survive
    assert c.post("/api/accounts", json={"bank": "B", "last4": "4111111111116677"}).json()["last4"] == "6677"
    assert c.post("/api/accounts", json={"bank": "B", "last4": "12x4"}).status_code == 400
    assert c.post("/api/accounts", json={"bank": "B", "last4": "123"}).status_code == 400
    assert c.post("/api/accounts", json={"bank": "", "last4": "1234"}).status_code == 400
    assert c.post("/api/accounts", json={"bank": "B", "last4": "1234", "kind": "wallet"}).status_code == 400


def test_duplicate_card_is_rejected(db_session, client_for):
    h = _hh(db_session)
    c = client_for(h)
    body = {"bank": "B", "last4": "1234"}
    assert c.post("/api/accounts", json=body).status_code == 200
    assert c.post("/api/accounts", json=body).status_code == 400


def test_accounts_never_leak_across_households(db_session, client_for):
    """The hard one. These rows hold account numbers; one household must never see another's."""
    a, b = _hh(db_session, "A"), _hh(db_session, "B")
    client_for(a).post("/api/accounts", json={"bank": "B", "last4": "1111"})
    assert client_for(b).get("/api/accounts").json()["accounts"] == []
    # ...and must not be able to reach it by guessing the id
    aid = client_for(a).get("/api/accounts").json()["accounts"][0]["id"]
    assert client_for(b).delete(f"/api/accounts/{aid}").status_code == 404
    assert client_for(b).patch(f"/api/accounts/{aid}", json={"bank": "X", "last4": "9999"}).status_code == 404
    assert len(client_for(a).get("/api/accounts").json()["accounts"]) == 1


def test_unknown_senders_never_offers_an_otp_sender(db_session, client_for, monkeypatch):
    """An OTP header is bank-shaped but carries a live credential — it must never be offered as an
    account to register, or we would start ingesting one-time passwords."""
    h = _hh(db_session)
    db_session.add(Account(household_id=h.id, bank="B", kind="card", last4="1234",
                           sms_senders="KNOWNBK"))
    db_session.commit()
    import services.sms_capture as sc
    monkeypatch.setattr(sc, "scan_all_senders", lambda since_days=90: [
        {"sender": "VD-NEWBANK-S", "count": 4, "last_seen": "2026-08-04T10:00:00",
         "sample": "spent", "transactional": True},
        {"sender": "JK-KNOWNBK-S", "count": 9, "last_seen": "2026-08-04T10:00:00",
         "sample": "spent", "transactional": True},
        {"sender": "AX-SOMEOTP-S", "count": 7, "last_seen": "2026-08-04T10:00:00",
         "sample": "code", "transactional": True},
        # a delivery/marketing shortcode: never reports a debit, so it is not an account
        {"sender": "VM-SHOPCO-S", "count": 60, "last_seen": "2026-08-04T10:00:00",
         "sample": "your order is arriving", "transactional": False},
    ])
    out = client_for(h).get("/api/accounts/unknown-senders").json()
    names = [u["sender"] for u in out["unknown"]]
    assert "VD-NEWBANK-S" in names            # genuinely unrecognised -> offered
    assert "JK-KNOWNBK-S" not in names        # already claimed by a registered account
    assert "AX-SOMEOTP-S" not in names        # OTP -> never offered
    assert "VM-SHOPCO-S" not in names         # never reports a debit -> not an account
    assert out["unknown"][0]["suggested_token"] == "NEWBANK"


def test_unknown_senders_degrades_quietly_without_messages_access(db_session, client_for, monkeypatch):
    """On a machine with no Full Disk Access the screen must render empty, not 500."""
    h = _hh(db_session)
    import services.sms_capture as sc

    def boom(since_days=90):
        raise PermissionError("no Full Disk Access")

    monkeypatch.setattr(sc, "scan_all_senders", boom)
    r = client_for(h).get("/api/accounts/unknown-senders")
    assert r.status_code == 200
    assert r.json() == {"unknown": [], "available": False}


def test_an_inactive_account_excludes_its_card_from_ingest(db_session):
    """A card that reaches this mailbox but is not the household's (an employer card, a parent's card
    on a shared email) must never be ingested.

    This is a regression test with a real cost behind it: the exclusion used to live as a list of card
    numbers in config.py. Emptying that list without migrating the values into the accounts table let
    an office card's spend into the household ledger.
    """
    from services.accounts import excluded_last4

    h = _hh(db_session)
    db_session.add(Account(household_id=h.id, bank="Excluded", kind="card", last4="4321",
                           label="office card", active=False))
    db_session.add(Account(household_id=h.id, bank="B", kind="card", last4="1111", active=True))
    db_session.commit()

    got = excluded_last4(db_session, h.id)
    assert "4321" in got, "an inactive account must exclude its card from ingest"
    assert "1111" not in got, "an active account must NOT be excluded"


def test_exclusions_do_not_leak_between_households(db_session):
    from services.accounts import excluded_last4

    a, b = _hh(db_session, "A"), _hh(db_session, "B")
    db_session.add(Account(household_id=a.id, bank="X", kind="card", last4="4321", active=False))
    db_session.commit()
    assert "4321" in excluded_last4(db_session, a.id)
    assert "4321" not in excluded_last4(db_session, b.id)


def test_an_excluded_account_named_only_in_prose_is_still_blocked():
    """The guard must not depend on the extractor succeeding.

    Real incident: HDFC InstaAlerts say "Rs.5000.00 is debited from your account ending 5566", with
    the number in prose rather than a field. The extractor missed it, the row was stored as "0000",
    and the exclusion compared "0000" against the list — so a non-household account's UPI transfers
    were ingested as household spending.
    """
    from services.accounts import is_excluded_text, last4s_mentioned

    excluded = {"4321", "5566"}
    real_alerts = [
        "Dear Customer, Rs.5000.00 is debited from your account ending 5566 towards VPA "
        "9000000000@pthdfc (SOME PAYEE NAME) on 01-08-26.",
        "Rs.2000.00 is debited from your account ending 5566 towards VPA someone@oksbi on 02-08-26.",
        "Your a/c no. XXXXXXXX4321 has been debited for Rs 25000.",
    ]
    for text in real_alerts:
        assert is_excluded_text(text, excluded), f"not blocked: {text[:60]}"

    # ...and the household's own cards must still get through untouched
    for text in ("Spent INR 68 Axis Bank Card no. XX7788 on 02-08-26",
                 "Rs.65000 Credited to A/c ...4433 thru NEFT",
                 "Rs 892 spent on HDFC Card ending 9922 at BLINKIT"):
        assert is_excluded_text(text, excluded) is None, f"wrongly blocked: {text[:60]}"

    assert last4s_mentioned("debited from your account ending 5566") == {"5566"}
    assert last4s_mentioned("no account here") == set()
    assert last4s_mentioned(None) == set()


def test_a_three_digit_mask_resolves_to_the_right_account(db_session):
    """ICICI writes "ICICI Bank Acc XX456" — three digits, not four.

    Taking the last four CHARACTERS of "XX456" gives "X456", cleaned to "0456": an account that does
    not exist. Thirty real transactions (home loan EMIs, education loan, groceries) were filed under
    that phantom instead of the ICICI account they came from.
    """
    from services.accounts import registered_accounts, resolve_last4_from_text

    h = _hh(db_session)
    for last4, bank in (("3456", "ICICI"), ("8456", "HDFC"), ("9911", "ICICI"), ("4433", "Bank of Baroda")):
        db_session.add(Account(household_id=h.id, bank=bank, kind="account", last4=last4))
    db_session.commit()
    accts = registered_accounts(db_session, h.id)

    real = "ICICI Bank Acc XX456 debited Rs. 1,23,456.78 on 30-Jul-26 InfoEXMPLXX10203"
    assert resolve_last4_from_text(real, accts, bank_hint="AD-ICICIT-S") == "3456"

    # 456 alone is genuinely ambiguous (ICICI 3456 vs HDFC 8456) — refuse rather than guess, because
    # attributing to the wrong account is worse than leaving it unattributed
    assert resolve_last4_from_text("Acc XX456 debited", accts) is None
    # ...and the same partial resolves differently for a different sender
    assert resolve_last4_from_text("Card XX456 spent", accts, bank_hint="HDFCBK") == "8456"

    # shapes banks actually use
    assert resolve_last4_from_text("Credit Card Account 4xxx9911", accts, "ICICI") == "9911"
    assert resolve_last4_from_text("Credited to A/c ...4433 thru NEFT", accts) == "4433"
    assert resolve_last4_from_text("Transfer to A/c xxxxxxxxxx3456", accts) == "3456"

    # a loan account number is not a bank account and must not be claimed
    assert resolve_last4_from_text("EMI for Home Loan XX2468 is due", accts) is None
    # an unregistered account stays unresolved, so the exclusion path still sees it
    assert resolve_last4_from_text("debited from your account ending 5566", accts) is None


def test_unattributed_rows_are_repaired_from_their_own_description(db_session):
    """The resolver ran only at ingest against the raw SMS, so a row whose description NAMES the
    account stayed unattributed forever. "Transfer to A/c xxxxxxxxxx3456" says it outright."""
    from models.transaction import Transaction
    from services.accounts import repair_unattributed

    h = _hh(db_session)
    db_session.add(Account(household_id=h.id, bank="ICICI", kind="account", last4="3456"))
    db_session.add(Account(household_id=h.id, bank="HDFC", kind="card", last4="8456"))
    db_session.add(Account(household_id=h.id, bank="Excluded", kind="card", last4="5566", active=False))
    db_session.commit()

    def txn(**kw):
        base = dict(household_id=h.id, date="2026-06-04", amount=100.0, direction="debit",
                    fingerprint=f"fp{kw.get('description','')[:20]}{kw.get('amount',100)}",
                    bank="Sms", card_type="SMS", card_last4="0000", stmt_month="2026-06")
        base.update(kw)
        t = Transaction(**base)
        db_session.add(t)
        return t

    named = txn(description="Transfer to A/c xxxxxxxxxx3456", merchant="Transfer", amount=14000.0)
    ambiguous = txn(description="Acc XX456 debited", merchant="x", amount=500.0, fingerprint="fp2")
    hinted = txn(description="Acc XX456 debited", merchant="y", amount=501.0,
                 bank="ICICI", fingerprint="fp3")
    excluded_row = txn(description="debited from your account ending 5566", merchant="z",
                       amount=5000.0, fingerprint="fp4")
    nothing = txn(description="BESCOM", merchant="BESCOM", amount=456.0, fingerprint="fp5")
    db_session.commit()

    out = repair_unattributed(db_session, h.id)
    db_session.refresh(named); db_session.refresh(ambiguous)
    db_session.refresh(hinted); db_session.refresh(excluded_row); db_session.refresh(nothing)

    assert named.card_last4 == "3456", "the account is named in the description"
    assert named.bank == "ICICI", "bank/type are corrected too, not just the number"
    assert ambiguous.card_last4 == "0000", "456 matches two accounts and no hint — must not guess"
    assert hinted.card_last4 == "3456", "the bank column disambiguates the same text"
    assert excluded_row.card_last4 == "0000", "an EXCLUDED account must never be adopted in"
    assert nothing.card_last4 == "0000", "no account named — left alone"
    assert out["fixed"] == 2


def test_repair_is_idempotent_and_supports_dry_run(db_session):
    from models.transaction import Transaction
    from services.accounts import repair_unattributed

    h = _hh(db_session)
    db_session.add(Account(household_id=h.id, bank="ICICI", kind="account", last4="3456"))
    db_session.add(Transaction(household_id=h.id, date="2026-06-04", amount=1.0, direction="debit",
                               fingerprint="fpx", bank="Sms", card_type="SMS", card_last4="0000",
                               stmt_month="2026-06", description="A/c xxxxxxxxxx3456"))
    db_session.commit()

    dry = repair_unattributed(db_session, h.id, dry_run=True)
    assert dry["fixed"] == 1 and dry["dry_run"] is True
    assert db_session.query(Transaction).first().card_last4 == "0000", "dry run must not write"

    assert repair_unattributed(db_session, h.id)["fixed"] == 1
    assert repair_unattributed(db_session, h.id)["fixed"] == 0, "second run is a no-op"


def test_a_recurring_merchant_is_attributed_from_habit(db_session):
    """What a person would just know: this charge always comes off the same card.

    An EMI of exactly the same amount has debited one account nine months running; Dominos has only
    ever been on one card. When the alert names no account, habit is the answer.
    """
    from models.transaction import Transaction
    from services.accounts import repair_unattributed

    h = _hh(db_session)
    db_session.add(Account(household_id=h.id, bank="HDFC", kind="account", last4="7654"))
    db_session.add(Account(household_id=h.id, bank="HDFC", kind="account", last4="4455"))
    db_session.add(Account(household_id=h.id, bank="Axis", kind="card", last4="7788"))
    db_session.commit()

    def txn(last4, merchant, amount, fp, bank="HDFC"):
        db_session.add(Transaction(household_id=h.id, date="2026-06-01", amount=amount,
                                   direction="debit", fingerprint=fp, bank=bank,
                                   card_type="Account", card_last4=last4, stmt_month="2026-06",
                                   description=merchant, merchant=merchant))

    # a settled habit: the same EMI off the same account, repeatedly
    for i in range(4):
        txn("7654", "HDFC Bank Loan", 12345.0, f"emi{i}")
    # a merchant used on two banks' cards — the bank must decide, not the raw majority
    for i in range(3):
        txn("4455", "Amazon India", 500.0, f"az{i}")
    txn("7788", "Amazon India", 500.0, "azx", bank="Axis")
    db_session.commit()

    unknown_emi = Transaction(household_id=h.id, date="2026-07-07", amount=12345.0,
                              direction="debit", fingerprint="new1", bank="Sms",
                              card_type="SMS", card_last4="0000", stmt_month="2026-07",
                              description="HDFC Bank Loan", merchant="HDFC Bank Loan")
    unknown_axis = Transaction(household_id=h.id, date="2026-07-08", amount=500.0,
                               direction="debit", fingerprint="new2", bank="Axis",
                               card_type="Account", card_last4="0000", stmt_month="2026-07",
                               description="Amazon India", merchant="Amazon India")
    once_only = Transaction(household_id=h.id, date="2026-07-09", amount=42.0, direction="debit",
                            fingerprint="new3", bank="Sms", card_type="SMS", card_last4="0000",
                            stmt_month="2026-07", description="Never Seen", merchant="Never Seen")
    db_session.add_all([unknown_emi, unknown_axis, once_only])
    db_session.commit()

    repair_unattributed(db_session, h.id)
    db_session.refresh(unknown_emi); db_session.refresh(unknown_axis); db_session.refresh(once_only)

    assert unknown_emi.card_last4 == "7654", "same merchant AND amount, four times over"
    assert unknown_axis.card_last4 == "7788", "the bank narrows it before the majority does"
    assert once_only.card_last4 == "0000", "no history means no guess"


def test_a_single_prior_is_a_coincidence_not_a_habit(db_session):
    from models.transaction import Transaction
    from services.accounts import repair_unattributed

    h = _hh(db_session)
    db_session.add(Account(household_id=h.id, bank="HDFC", kind="account", last4="7654"))
    db_session.add(Account(household_id=h.id, bank="HDFC", kind="account", last4="4455"))
    db_session.commit()
    db_session.add(Transaction(household_id=h.id, date="2026-06-01", amount=99.0, direction="debit",
                               fingerprint="one", bank="HDFC", card_type="Account",
                               card_last4="7654", stmt_month="2026-06",
                               description="Rare Shop", merchant="Rare Shop"))
    t = Transaction(household_id=h.id, date="2026-07-01", amount=99.0, direction="debit",
                    fingerprint="two", bank="Sms", card_type="SMS", card_last4="0000",
                    stmt_month="2026-07", description="Rare Shop", merchant="Rare Shop")
    db_session.add(t)
    db_session.commit()
    repair_unattributed(db_session, h.id)
    db_session.refresh(t)
    assert t.card_last4 == "0000", "one earlier row is a coincidence, not a pattern"


def test_a_recurring_amount_beats_a_changing_merchant_label(db_session):
    """The same EMI arrives labelled differently month to month.

    The same amount comes off one account every month, but under "EMI 100200300" some months and
    "HDFC Bank Loan" others. Matching on merchant text missed it completely; the amount is what
    actually identifies it.
    """
    from models.transaction import Transaction
    from services.accounts import repair_unattributed

    h = _hh(db_session)
    db_session.add(Account(household_id=h.id, bank="HDFC", kind="account", last4="7654"))
    db_session.add(Account(household_id=h.id, bank="HDFC", kind="account", last4="4455"))
    db_session.commit()

    for i, label in enumerate(["EMI 100200300", "HDFC Bank Loan", "EMI 100200300", "Loan Repay"]):
        db_session.add(Transaction(household_id=h.id, date=f"2026-0{i+1}-07", amount=12345.0,
                                   direction="debit", fingerprint=f"e{i}", bank="HDFC",
                                   card_type="Account", card_last4="7654", stmt_month=f"2026-0{i+1}",
                                   description=label, merchant=label))
    # a ROUND recurring amount proves nothing — many things cost Rs 500
    for i in range(4):
        db_session.add(Transaction(household_id=h.id, date="2026-05-01", amount=500.0,
                                   direction="debit", fingerprint=f"r{i}", bank="HDFC",
                                   card_type="Account", card_last4="4455", stmt_month="2026-05",
                                   description=f"Thing {i}", merchant=f"Thing {i}"))
    db_session.commit()

    emi = Transaction(household_id=h.id, date="2026-07-07", amount=12345.0, direction="debit",
                      fingerprint="new-emi", bank="Sms", card_type="SMS", card_last4="0000",
                      stmt_month="2026-07", description="HDFC Bank Loan", merchant="HDFC Bank Loan")
    round_amt = Transaction(household_id=h.id, date="2026-07-07", amount=500.0, direction="debit",
                            fingerprint="new-round", bank="Sms", card_type="SMS", card_last4="0000",
                            stmt_month="2026-07", description="Something Else", merchant="Something Else")
    db_session.add_all([emi, round_amt])
    db_session.commit()

    repair_unattributed(db_session, h.id)
    db_session.refresh(emi); db_session.refresh(round_amt)
    assert emi.card_last4 == "7654", "a distinctive recurring amount identifies the account"
    assert round_amt.card_last4 == "0000", "a round recurring amount is not evidence"


def test_the_alert_sender_does_not_override_where_the_money_left_from(db_session):
    """An ICICI loan debiting an HDFC account is normal. Filtering candidates to the ALERT's bank
    would discard the true answer, so the bank only narrows when it has history of its own."""
    from models.transaction import Transaction
    from services.accounts import repair_unattributed

    h = _hh(db_session)
    db_session.add(Account(household_id=h.id, bank="HDFC", kind="account", last4="7654"))
    db_session.add(Account(household_id=h.id, bank="ICICI", kind="account", last4="3456"))
    db_session.commit()
    for i in range(3):
        db_session.add(Transaction(household_id=h.id, date="2026-0%d-05" % (i + 1), amount=2345.0,
                                   direction="debit", fingerprint=f"ic{i}", bank="HDFC",
                                   card_type="Account", card_last4="7654",
                                   stmt_month="2026-0%d" % (i + 1),
                                   description="ICICI Bank", merchant="ICICI Bank"))
    t = Transaction(household_id=h.id, date="2026-07-05", amount=2345.0, direction="debit",
                    fingerprint="ic-new", bank="ICICI", card_type="SMS", card_last4="0000",
                    stmt_month="2026-07", description="ICICI Bank", merchant="ICICI Bank")
    db_session.add(t)
    db_session.commit()
    repair_unattributed(db_session, h.id)
    db_session.refresh(t)
    assert t.card_last4 == "7654", "ICICI alert, HDFC account — history wins over the sender"


def test_a_debit_matching_a_tracked_emi_is_tagged_as_one(db_session):
    """Bank debit ADVICE is not an EMI reminder.

    The reminder ("EMI of Rs 5000 for Example Bank Loan") classifies correctly. The advice
    says "InfoBIL*Education", which matched the word Education and put a loan repayment in the same
    bucket as school fees. The loan table holds the exact EMI, so match on that instead.
    """
    from models.household import Loan
    from models.transaction import Transaction
    from services.commitments import tag_loan_emis

    h = _hh(db_session)
    from api.debt import compute_debt

    edu = Loan(household_id=h.id, name="Education Loan", lender="ICICI", holder="A",
               category="Education", original_principal=500000.0, annual_rate_pct=9.0,
               tenure_months=60, disbursement_date=__import__("datetime").date(2024, 1, 1))
    db_session.add(edu)
    db_session.commit()
    emi_amount = round(next(l["emi_inr"] for l in compute_debt(db_session, h.id)["loans"]))
    assert emi_amount % 100 != 0, "fixture must use a distinctive (non-round) EMI"

    def txn(amount, desc, fp):
        t = Transaction(household_id=h.id, date="2026-08-05", amount=amount, direction="debit",
                        fingerprint=fp, bank="ICICI", card_type="Account", card_last4="3456",
                        stmt_month="2026-08", description=desc, merchant=desc,
                        category="Education", txn_type="SPEND")
        db_session.add(t)
        return t

    emi = txn(float(emi_amount), "InfoBIL*Education", "a")
    unrelated = txn(750.0, "Bookstore", "c")
    db_session.commit()

    out = tag_loan_emis(db_session, h.id)
    for t in (emi, unrelated):
        db_session.refresh(t)

    assert emi.txn_type == "EMI" and emi.category == "Loan EMI"
    assert unrelated.txn_type == "SPEND"
    assert out["tagged"] == 1


def test_tagging_emis_never_touches_a_credit(db_session):
    """A refund or disbursal of the same amount is not a repayment."""
    from models.household import Loan
    from models.transaction import Transaction
    from services.commitments import tag_loan_emis

    h = _hh(db_session)
    from api.debt import compute_debt

    loan = Loan(household_id=h.id, name="L", lender="X", holder="A", category="Other",
                original_principal=500000.0, annual_rate_pct=9.0, tenure_months=60,
                disbursement_date=__import__("datetime").date(2024, 1, 1))
    db_session.add(loan)
    db_session.commit()
    amt = float(round(next(l["emi_inr"] for l in compute_debt(db_session, h.id)["loans"])))
    t = Transaction(household_id=h.id, date="2026-08-05", amount=amt, direction="credit",
                    fingerprint="cr", bank="ICICI", card_type="Account", card_last4="3456",
                    stmt_month="2026-08", description="Refund", merchant="Refund",
                    category="Education", txn_type="SPEND")
    db_session.add(t)
    db_session.commit()
    tag_loan_emis(db_session, h.id)
    db_session.refresh(t)
    assert t.txn_type == "SPEND", "a credit is not an EMI payment"
