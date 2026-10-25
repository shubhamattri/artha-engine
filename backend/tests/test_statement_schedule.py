"""The monthly statement audit is keyed to each card's own date, and never silently skips a month."""

from datetime import date

from models.account import Account
from models.tenant import Household
from services.statement_schedule import due_today, subject_shape


def _hh(db):
    h = Household(name="H")
    db.add(h)
    db.commit()
    return h


def _acct(db, h, **kw):
    a = Account(household_id=h.id, bank="B", kind="card", last4=kw.pop("last4", "1234"), **kw)
    db.add(a)
    db.commit()
    return a


def test_not_due_before_the_run_day(db_session):
    h = _hh(db_session)
    _acct(db_session, h, statement_sender="cards@bank.example", statement_day=22, statement_run_day=23)
    assert due_today(db_session, h.id, today=date(2026, 8, 22)) == []


def test_due_on_the_run_day(db_session):
    h = _hh(db_session)
    _acct(db_session, h, statement_sender="cards@bank.example", statement_day=22, statement_run_day=23)
    assert len(due_today(db_session, h.id, today=date(2026, 8, 23))) == 1


def test_a_missed_day_does_not_skip_the_whole_month(db_session):
    """Server asleep, no network, machine rebooting — the audit must still catch up rather than wait
    another month. This is the failure that makes a monthly job worse than useless."""
    h = _hh(db_session)
    _acct(db_session, h, statement_sender="cards@bank.example", statement_day=22, statement_run_day=23)
    assert len(due_today(db_session, h.id, today=date(2026, 8, 27))) == 1


def test_not_due_once_this_months_statement_is_in(db_session):
    h = _hh(db_session)
    _acct(db_session, h, statement_sender="cards@bank.example", statement_day=22,
          statement_run_day=23, statement_last_imported="2026-07")
    assert due_today(db_session, h.id, today=date(2026, 8, 25)) == []
    # ...but next month it is due again
    assert len(due_today(db_session, h.id, today=date(2026, 9, 25))) == 1


def test_an_account_with_no_sender_is_never_due(db_session):
    """Nothing to fetch from, so it must not appear as due work forever."""
    h = _hh(db_session)
    _acct(db_session, h, statement_day=22)
    assert due_today(db_session, h.id, today=date(2026, 8, 28)) == []


def test_run_day_defaults_to_the_day_after_arrival(db_session):
    h = _hh(db_session)
    _acct(db_session, h, statement_sender="cards@bank.example", statement_day=6)
    assert due_today(db_session, h.id, today=date(2026, 8, 6)) == []      # day of, too early
    assert len(due_today(db_session, h.id, today=date(2026, 8, 7))) == 1  # day after


def test_households_are_isolated(db_session):
    a = _hh(db_session)
    b = _hh(db_session)
    _acct(db_session, a, statement_sender="cards@bank.example", statement_day=1, statement_run_day=2)
    assert len(due_today(db_session, a.id, today=date(2026, 8, 5))) == 1
    assert due_today(db_session, b.id, today=date(2026, 8, 5)) == []


# --- one address, two cards -------------------------------------------------
# One bank mails two cards' statements on different days from ONE address.
# Keyed on sender alone the two merge into a series landing on the 3rd and the 13th, which reads as
# a 10-day spread and gets marked unstable — so the two cards that most needed a schedule were the
# two excluded from it. The subject is what tells them apart.

AMAZON_JUN = "Amazon Pay ICICI Bank Credit Card Statement for the period June 13 2026 to July 12 2026"
AMAZON_JUL = "Amazon Pay ICICI Bank Credit Card Statement for the period July 13 2026 to August 12 2026"
REWARDS_JUL = "ICICI Bank Credit Card Statement for the period  July 3 2026 to August 2 2026"


def test_same_card_collapses_across_months():
    """The dates change every month; the shape must not, or every month looks like a new card."""
    assert subject_shape(AMAZON_JUN) == subject_shape(AMAZON_JUL)


def test_two_cards_on_one_sender_stay_apart():
    assert subject_shape(AMAZON_JUL) != subject_shape(REWARDS_JUL)


def test_a_masked_card_number_survives_normalisation():
    """Dates and card numbers are both digits. Stripping both would merge the very cards this is
    meant to separate, so only date-shaped tokens go."""
    a = subject_shape("HDFC Bank Credit Card Statement XXXX2468 for Jul 2026")
    b = subject_shape("HDFC Bank Credit Card Statement XXXX9922 for Jul 2026")
    assert a != b
    assert "xxxx2468" in a


def test_the_subject_hint_selects_one_card_not_both(db_session):
    """Both ICICI cards are due on their own days; neither may swallow the other's statement."""
    h = _hh(db_session)
    amazon = _acct(db_session, h, last4="9911", statement_sender="credit_cards@icici.bank.in",
                   statement_day=13, statement_run_day=14,
                   statement_subject_hint=subject_shape(AMAZON_JUL))
    # Its own statement is already in, so it is settled for this month and must not come back.
    _acct(db_session, h, last4="3010", statement_sender="credit_cards@icici.bank.in",
          statement_day=3, statement_run_day=4, statement_last_imported="2026-07",
          statement_subject_hint=subject_shape(REWARDS_JUL))

    due = due_today(db_session, h.id, today=date(2026, 8, 14))
    assert [a.last4 for a in due] == ["9911"], "only the first card is due on the 14th"
    assert amazon.statement_subject_hint not in subject_shape(REWARDS_JUL), \
        "the hint must not match the other card's statement"


def test_an_unimported_card_stays_due_after_its_day_passes(db_session):
    """The second card's statement lands on the 3rd. If nothing imported it, it is still owed on the
    14th — that overdue state is the point of the schedule, not a bug in it."""
    h = _hh(db_session)
    _acct(db_session, h, last4="3010", statement_sender="credit_cards@icici.bank.in",
          statement_day=3, statement_run_day=4)
    assert [a.last4 for a in due_today(db_session, h.id, today=date(2026, 8, 14))] == ["3010"]


def test_a_month_glued_to_its_year_is_still_a_date():
    """HDFC writes "April-2026" and Kotak "Apr-2026" as one token. Splitting on whitespace alone left
    them intact, so every month became its own shape and 15 samples of one card read as 15 series of
    one — every card then looked unstable and none got scheduled."""
    a = subject_shape("Your HDFC Bank Swiggy HDFC Bank Credit Card Statement April-2026")
    b = subject_shape("Your HDFC Bank Swiggy HDFC Bank Credit Card Statement March-2026")
    assert a == b
    assert "2026" not in a
    assert subject_shape("Apr-2026 Statement for Classic Credit Card X6543") == \
           subject_shape("Mar-2026 Statement for Classic Credit Card X6543")


def test_a_domain_migration_does_not_drop_a_card():
    """hdfcbank.net -> hdfcbank.bank.in and icicibank.com -> icici.bank.in both happened. An exact
    sender list silently stopped recognising the new address."""
    from services.statement_email import card_issuer
    assert card_issuer("emailstatements.cards@hdfcbank.bank.in") == ("HDFC", "Credit Card")
    assert card_issuer("credit_cards@icicibank.com") == ("ICICI", "Credit Card")


def test_an_account_statement_is_not_a_card_statement():
    """Same banks, same domains, different mailbox. Letting these through would feed the card parsers
    files they cannot read and count the failures as breakage."""
    from services.statement_email import card_issuer
    assert card_issuer("estatement@icicibank.com") is None
    assert card_issuer("bankstatements@kotak.bank.in") is None
    assert card_issuer("hdfcbanksmartstatement@hdfcbank.bank.in") is None


# --- billing cycle bounds ---------------------------------------------------

def test_a_purchase_on_the_closing_day_is_in_the_closing_cycle():
    """The first card runs 13th to 12th. Spending on the 12th belongs to the cycle that closes
    that day and bills tomorrow — it does not roll into the next one. Confirmed against six parsed
    statements: February's ends 12 Feb and March's starts 13 Feb, adjacent with no gap."""
    from services.statement_schedule import card_cycle_bounds
    start, end = card_cycle_bounds(13, date(2026, 8, 12))
    assert (start, end) == (date(2026, 7, 13), date(2026, 8, 12))


def test_the_day_after_closing_opens_the_next_cycle():
    from services.statement_schedule import card_cycle_bounds
    assert card_cycle_bounds(13, date(2026, 8, 13)) == (date(2026, 8, 13), date(2026, 9, 12))


def test_a_short_month_does_not_produce_an_invalid_date():
    """A card billing on the 31st has no 31 February to close on."""
    from services.statement_schedule import card_cycle_bounds
    start, end = card_cycle_bounds(31, date(2026, 2, 15))
    assert start == date(2026, 1, 31)
    assert end == date(2026, 2, 27)      # day before 28 Feb, the clamped next start


def test_saving_a_new_cycle_day_reports_the_new_day(db_session):
    """The resolver memoises per session on the assumption that config cannot change mid-request.
    The setter is the one caller that breaks that, and without invalidation it answered with the old
    day — so the UI showed the save as a no-op and the change looked rejected."""
    from api.meta import get_household_cycle, set_household_cycle
    h = _hh(db_session)
    h.cycle_start_day = 12
    db_session.commit()
    assert get_household_cycle(db=db_session, household=h)["cycle_start_day"] == 12   # warms the memo
    out = set_household_cycle(payload={"cycle_start_day": 13}, db=db_session, household=h)
    assert out["cycle_start_day"] == 13, "the setter must report the day it just saved"
    assert out["start"].endswith("-13") or out["start"][-2:] == "13"


# --- unbilled statements are provisional ------------------------------------

def test_a_current_export_is_recognised_as_unbilled():
    """The bank's mid-cycle export lists transactions the billed statement will list again in a
    different format, so importing both as authoritative counted the same spend twice."""
    from services.ingest import is_unbilled_statement
    assert is_unbilled_statement("CCStatement_Current24-06-2026.xls")
    assert is_unbilled_statement("/tmp/x/unbilled_transactions.pdf")


def test_a_billed_statement_is_not_treated_as_unbilled():
    """Getting this backwards is worse than the bug: the authoritative statement would be marked
    provisional and archived by the next import."""
    from services.ingest import is_unbilled_statement
    assert not is_unbilled_statement("4111XXXXXXXX9911_1000001_Retail_NORM.pdf")
    assert not is_unbilled_statement("ICICI_AmazonPay_2026-05.pdf")
    assert not is_unbilled_statement("4854XXXXXXXXXX42_21-07-2026_556.pdf")
