"""Windfall — "prepay the debt or invest it" laid side by side, for a lump sum.

The two things this endpoint exists to get right, and the two it is easiest to get wrong:

  1. The prepay side must be the SAME arithmetic the Debt tab already runs. A second amortisation
     implementation drifts, and the drift reads as a finding. So the baseline a loan is measured
     against here has to equal the forward schedule api/debt already publishes for that loan.

  2. The invest side must NOT be one number from one assumed rate. A hardcoded 12%/yr is exactly how
     this app once reported a portfolio 41% above reality. It returns a RANGE of stated rates, each
     carrying its own assumption, anchored on the household's own realised XIRR.

And the whole thing is a calculator: it presents both and stops. No recommendation, no winner.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from models.household import CashflowLine, Loan
from models.recurring import RecurringInvestment
from models.tenant import Household


def _household(db, name="H"):
    h = Household(name=name)
    db.add(h)
    db.commit()
    return h


def _loan(db, h, name, principal, rate, tenure=60, **kw):
    kw.setdefault("rate_type", "floating")      # RBI: freely part-prepayable, so no fee noise
    kw.setdefault("category", "Home")
    loan = Loan(household_id=h.id, name=name, lender="L", holder="A",
                original_principal=principal, annual_rate_pct=rate, tenure_months=tenure,
                disbursement_date=date(2024, 1, 10), **kw)
    db.add(loan)
    db.commit()
    return loan


def _win(db, h, amount=None, horizon_months=None):
    from api.windfall import compute_windfall
    return compute_windfall(db, h.id, amount, horizon_months)


# ── the prepay side ──────────────────────────────────────────────────────────

def test_prepay_takes_the_highest_nominal_rate_first(db_session):
    """Order is by the CONTRACTED headline rate, highest first — not by balance, not by EMI."""
    h = _household(db_session)
    _loan(db_session, h, "Cheap", 500_000, 8.0)
    _loan(db_session, h, "Dear", 200_000, 18.0)
    _loan(db_session, h, "Middle", 400_000, 12.0)

    out = _win(db_session, h, 100_000)
    names = [ln["name"] for ln in out["prepay"]["loans"]]
    assert names[0] == "Dear", names
    assert out["prepay"]["order"] == "highest nominal rate first"


def test_prepay_clears_loans_in_rate_order_until_the_money_runs_out(db_session):
    h = _household(db_session)
    dear = _loan(db_session, h, "Dear", 200_000, 18.0)
    _loan(db_session, h, "Cheap", 500_000, 8.0)

    from api.debt import compute_debt
    bal = next(ln["balance_inr"] for ln in compute_debt(db_session, h.id)["loans"]
               if ln["id"] == dear.id)

    out = _win(db_session, h, bal + 1_000)
    assert out["prepay"]["cleared"] == ["Dear"]
    row = next(ln for ln in out["prepay"]["loans"] if ln["name"] == "Dear")
    assert row["cleared"] is True
    # The tail flows on to the next loan in the order; it is never destroyed.
    assert any(ln["name"] == "Cheap" and ln["applied_inr"] > 0 for ln in out["prepay"]["loans"])
    assert out["prepay"]["amount_unused_inr"] == 0.0


def test_prepay_baseline_is_the_schedule_api_debt_already_publishes(db_session):
    """No second amortisation. With no surplus to redirect, the payoff plan for a lone loan IS its
    contracted schedule, so the months and interest this page measures against must equal the forward
    schedule the Debt detail endpoint already serves for it. (With a surplus the baseline is the plan
    including that surplus — see the two-streams section at the end of this file.)"""
    h = _household(db_session)
    loan = _loan(db_session, h, "Only", 600_000, 11.0, tenure=84)

    from api.debt import get_loan_detail
    sched = get_loan_detail(loan.id, db=db_session, household=h)["forward_schedule"]

    out = _win(db_session, h, 1.0)
    row = next(ln for ln in out["prepay"]["loans"] if ln["id"] == loan.id)
    assert row["months_before"] == len(sched)
    assert row["interest_before_inr"] == pytest.approx(sched[-1]["interest_paid_so_far"], abs=1.0)


def test_interest_avoided_on_a_cleared_loan_is_its_whole_remaining_interest(db_session):
    h = _household(db_session)
    loan = _loan(db_session, h, "Only", 300_000, 14.0, tenure=48)

    from api.debt import get_loan_detail
    sched = get_loan_detail(loan.id, db=db_session, household=h)["forward_schedule"]
    remaining_interest = sched[-1]["interest_paid_so_far"]

    from api.debt import compute_debt
    bal = next(ln["balance_inr"] for ln in compute_debt(db_session, h.id)["loans"])

    out = _win(db_session, h, bal)
    assert out["prepay"]["interest_avoided_inr"] == pytest.approx(remaining_interest, abs=1.0)
    assert out["prepay"]["months_removed"] == len(sched)


def test_a_partial_prepay_frees_no_emi_and_a_full_clear_frees_exactly_that_emi(db_session):
    """EMI is contractual: prepaying part of a loan shortens the tenure, it does not lower the
    instalment. Only a loan that actually closes hands its EMI back."""
    h = _household(db_session)
    loan = _loan(db_session, h, "Only", 500_000, 10.0, tenure=60)
    from api.debt import compute_debt
    view = compute_debt(db_session, h.id)["loans"][0]

    part = _win(db_session, h, view["balance_inr"] / 2.0)
    assert part["prepay"]["emi_freed_per_month_inr"] == 0.0
    assert part["prepay"]["cleared"] == []
    # It still buys something: the loan ends sooner and costs less interest.
    row = next(ln for ln in part["prepay"]["loans"] if ln["id"] == loan.id)
    assert row["months_after"] < row["months_before"]
    assert row["interest_avoided_inr"] > 0

    full = _win(db_session, h, view["balance_inr"])
    assert full["prepay"]["emi_freed_per_month_inr"] == pytest.approx(view["emi_inr"], abs=1.0)


def test_loans_the_household_does_not_service_are_never_prepaid(db_session):
    """Same two flags api/debt reads: serviced_by_household says whose cash-flow it is, and
    beneficiary='external' says whose principal it clears. Prepaying either spends this household's
    money on somebody else's balance sheet."""
    h = _household(db_session)
    _loan(db_session, h, "Parent's own", 400_000, 16.0,
          serviced_by_household=False, foir_exempt=True)
    _loan(db_session, h, "Sibling's, I pay", 400_000, 15.0, beneficiary="external")
    _loan(db_session, h, "Mine", 400_000, 9.0)

    out = _win(db_session, h, 10_000_000)
    touched = {ln["name"] for ln in out["prepay"]["loans"]}
    assert touched == {"Mine"}
    excluded = {e["name"] for e in out["prepay"]["excluded"]}
    assert excluded == {"Parent's own", "Sibling's, I pay"}
    assert all(e["reason"] for e in out["prepay"]["excluded"])


def test_foir_exempt_alone_does_not_remove_a_loan_the_household_services(db_session):
    """foir_exempt is the LENDER-ELIGIBILITY axis, not the whose-money axis — compute_debt keeps such
    a loan in its totals and its attack order, so this must too."""
    h = _household(db_session)
    _loan(db_session, h, "Off FOIR but mine", 300_000, 13.0,
          foir_exempt=True, serviced_by_household=True)

    out = _win(db_session, h, 50_000)
    assert [ln["name"] for ln in out["prepay"]["loans"]] == ["Off FOIR but mine"]
    assert out["prepay"]["excluded"] == []


def test_a_lender_that_refuses_part_payment_is_not_promised_one(db_session):
    """A fixed-rate car loan is foreclose-only by RBI-derived default. Money too small to clear it
    passes on to the next loan rather than being modelled into a payment the lender hands back."""
    h = _household(db_session)
    _loan(db_session, h, "Locked car", 600_000, 15.0, rate_type="fixed", category="Car")
    _loan(db_session, h, "Open home", 400_000, 9.0)

    out = _win(db_session, h, 50_000)
    assert [ln["name"] for ln in out["prepay"]["loans"]] == ["Open home"]
    assert any(e["name"] == "Locked car" for e in out["prepay"]["excluded"])


def test_a_gold_loan_is_prepaid_against_principal_not_accrued_interest(db_session):
    """balance_inr on a gold loan carries accrued-but-unpaid interest, which no payment amortises.
    api/debt simulates the PRINCIPAL; feeding the combined figure charges interest on interest."""
    h = _household(db_session)
    # Serviced under its own monthly interest (~3,792), which is the bullet shape: principal sits
    # still and the unpaid interest piles onto balance_inr.
    _loan(db_session, h, "Gold", 500_000, 9.1, tenure=12,
          loan_type="gold", expected_monthly_payment=2_000)

    from api.debt import compute_debt
    view = compute_debt(db_session, h.id)["loans"][0]
    assert view["balance_inr"] > view["principal_outstanding"]     # interest has accrued

    out = _win(db_session, h, view["principal_outstanding"])
    row = next(ln for ln in out["prepay"]["loans"] if ln["name"] == "Gold")
    assert row["balance_inr"] == pytest.approx(view["principal_outstanding"], abs=1.0)
    assert row["cleared"] is True


def test_zero_changes_nothing(db_session):
    h = _household(db_session)
    _loan(db_session, h, "Only", 500_000, 10.0)

    out = _win(db_session, h, 0.0)
    p = out["prepay"]
    assert (p["interest_avoided_inr"], p["emi_freed_per_month_inr"], p["months_removed"]) == (0.0, 0.0, 0)
    assert p["cleared"] == []
    assert all(s["gain_inr"] == 0.0 for s in out["invest"]["scenarios"])


def test_a_negative_amount_is_refused(db_session):
    from fastapi import HTTPException
    h = _household(db_session)
    with pytest.raises(HTTPException) as e:
        _win(db_session, h, -1.0)
    assert e.value.status_code == 400


# ── the invest side ──────────────────────────────────────────────────────────

def test_invest_returns_a_range_of_stated_rates_not_one_number(db_session):
    """The whole reason this feature exists: one assumed rate presented as an answer is how a
    portfolio got reported 41% above reality."""
    h = _household(db_session)
    out = _win(db_session, h, 500_000)
    inv = out["invest"]

    assert inv["single_rate_used"] is False
    rates = [s["rate_pct"] for s in inv["scenarios"]]
    assert len(rates) >= 3
    assert len(set(rates)) == len(rates)          # distinct rates, not one repeated
    assert rates == sorted(rates)
    assert inv["range"]["low_value_inr"] < inv["range"]["high_value_inr"]


def test_every_invest_scenario_states_its_assumption_and_where_it_came_from(db_session):
    """A bare rate on screen is an invented figure. Each one names what it assumes."""
    h = _household(db_session)
    for s in _win(db_session, h, 100_000)["invest"]["scenarios"]:
        assert s["assumption"] and len(s["assumption"]) > 20, s
        assert s["source"] in ("observed", "assumed"), s
        assert s["label"], s
        if s["source"] == "assumed":
            assert "assum" in s["assumption"].lower(), s


def test_invest_anchors_on_the_households_own_realised_xirr(db_session):
    """The household's measured money-weighted return is available per-investment from
    api/recurring. Using it as an anchor is the difference between an observation and a guess."""
    h = _household(db_session)
    now = datetime.now(timezone.utc).isoformat()
    db_session.add(RecurringInvestment(
        household_id=h.id, kind="sip", name="Fund", opening_balance=100_000.0,
        as_of_date=date.today() - timedelta(days=400), monthly_contribution=0.0,
        annual_rate_pct=12.0, compounding="monthly",
        market={"scheme_code": "1", "units": 1000.0, "last_nav": 150.0,
                "nav_date": "2026-08-18", "last_nav_at": now}))
    db_session.commit()

    from api.recurring import compute_recurring
    own = next(i for i in compute_recurring(db_session, h.id)["investments"])
    assert own.get("xirr_pct") is not None, "fixture did not produce a realised XIRR"

    out = _win(db_session, h, 100_000)
    observed = [s for s in out["invest"]["scenarios"] if s["source"] == "observed"]
    assert len(observed) == 1
    assert observed[0]["rate_pct"] == pytest.approx(own["xirr_pct"], abs=0.1)
    assert "xirr" in observed[0]["assumption"].lower()


def test_no_realised_xirr_is_said_out_loud_rather_than_filled_in(db_session):
    """With nothing measured, the anchor is absent and the reason is published — never replaced by
    an invented figure that looks like a measurement."""
    h = _household(db_session)
    out = _win(db_session, h, 100_000)
    assert [s for s in out["invest"]["scenarios"] if s["source"] == "observed"] == []
    assert out["invest"]["realised_xirr_unavailable_why"]


def test_both_paths_are_projected_over_the_same_horizon(db_session):
    """One clock for both sides. It is the published horizon — NOT the remaining life of whichever
    loan happens to run longest, which is what this used to pin (see the horizon section below)."""
    h = _household(db_session)
    _loan(db_session, h, "Only", 600_000, 11.0, tenure=84)
    out = _win(db_session, h, 50_000)
    assert out["invest"]["horizon_months"] == out["horizon_months"]
    assert out["prepay"]["horizon_months"] == out["horizon_months"]
    assert out["horizon_basis"]


# ── the structural difference, and the refusal to advise ─────────────────────

def test_certain_and_uncertain_are_machine_readable(db_session):
    h = _household(db_session)
    out = _win(db_session, h, 500_000)
    assert out["prepay"]["certainty"] == "certain"
    assert out["invest"]["certainty"] == "uncertain"
    assert out["certainty"]["prepay"]["kind"] == "certain"
    assert out["certainty"]["invest"]["kind"] == "uncertain"
    assert out["certainty"]["comparable"] is False
    assert out["certainty"]["comparable_why"]


def test_it_never_recommends_ranks_or_scores_either_path(db_session):
    """It is a calculator. It presents both and stops."""
    banned = {"recommended", "recommended_why", "recommendation", "better", "best", "winner",
              "score", "advice", "suggestion", "verdict", "preferred"}

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for v in node:
                yield from keys(v)

    h = _household(db_session)
    _loan(db_session, h, "Only", 500_000, 22.0)
    found = banned & set(keys(_win(db_session, h, 500_000)))
    assert not found, f"the calculator is taking a side: {found}"


def test_it_says_the_figures_are_projections_and_not_advice(db_session):
    h = _household(db_session)
    d = _win(db_session, h, 100_000)["disclaimer"].lower()
    assert "projection" in d
    assert "not financial advice" in d


# ── the default amount ───────────────────────────────────────────────────────

def test_the_amount_defaults_to_investable_cash_from_its_owners(db_session):
    """Cash on the balance sheet less the emergency buffer — read from api/networth and api/cashflow,
    never recomputed here."""
    from models.asset import Asset

    h = _household(db_session)
    db_session.add(Asset(household_id=h.id, name="Savings", asset_type="cash",
                         holder="A", manual_value_inr=2_000_000.0, active=True))
    db_session.add(CashflowLine(household_id=h.id, kind="income", label="Salary",
                                amount_inr=300_000.0, active=True))
    db_session.add(CashflowLine(household_id=h.id, kind="fixed", label="Rent",
                                amount_inr=50_000.0, active=True))
    db_session.commit()

    from api.cashflow import compute_cashflow
    from api.networth import compute_assets
    cash = compute_assets(db_session, h.id)["by_type"]["cash"]
    cf = compute_cashflow(db_session, h.id)

    out = _win(db_session, h, None)
    assert out["amount_source"] == "investable_cash"
    assert out["investable_cash"]["liquid_cash_inr"] == pytest.approx(cash, abs=1.0)
    assert out["investable_cash"]["emergency_buffer_inr"] == pytest.approx(
        6 * (cf["fixed"] + cf["leftover"]), abs=1.0)
    assert out["amount_inr"] == pytest.approx(out["investable_cash"]["amount_inr"], abs=0.01)


def test_an_explicit_amount_wins_and_says_so(db_session):
    h = _household(db_session)
    out = _win(db_session, h, 250_000)
    assert (out["amount_inr"], out["amount_source"]) == (250_000.0, "requested")


def test_the_route_publishes_what_the_helper_computes(db_session, client_for):
    """A helper having a key is not the same as the route serving it."""
    h = _household(db_session)
    _loan(db_session, h, "Only", 500_000, 10.0)
    body = client_for(h).get("/api/windfall?amount=100000").json()
    assert body["amount_inr"] == 100_000.0
    assert body["prepay"]["certainty"] == "certain"
    assert body["invest"]["scenarios"]


# ── the cross-module invariant ───────────────────────────────────────────────

def test_the_invariant_is_registered(db_session):
    from services.consistency import CHECKS, check_windfall_agrees_with_debt
    assert check_windfall_agrees_with_debt in CHECKS


def test_the_invariant_passes_when_windfall_attacks_exactly_what_debt_attacks(db_session):
    from services.consistency import Report, check_windfall_agrees_with_debt

    h = _household(db_session)
    _loan(db_session, h, "A", 500_000, 12.0)
    _loan(db_session, h, "B", 300_000, 8.0)

    r = Report()
    check_windfall_agrees_with_debt(db_session, h.id, r)
    f = next(x for x in r.findings if x.check == "windfall_matches_debt")
    assert f.severity == "ok", f.detail


def test_the_invariant_fails_when_the_two_sides_use_different_horizons(db_session):
    """Two horizons is two questions answered on one screen. The invariant pins the one clock."""
    from unittest.mock import patch

    from services.consistency import Report, check_windfall_agrees_with_debt

    h = _household(db_session)
    _loan(db_session, h, "A", 500_000, 12.0)

    real = __import__("api.windfall", fromlist=["compute_windfall"]).compute_windfall

    def skew(db, hid, amount=None, horizon_months=None):
        out = real(db, hid, amount, horizon_months)
        out["invest"]["horizon_months"] = out["horizon_months"] + 12
        return out

    r = Report()
    with patch("api.windfall.compute_windfall", skew):
        check_windfall_agrees_with_debt(db_session, h.id, r)
    f = next(x for x in r.findings if x.check == "windfall_matches_debt")
    assert f.severity == "error", f.detail


def test_the_invariant_fails_when_the_two_attack_sets_drift(db_session):
    """If api/debt changes which loans are attackable and windfall does not follow, that must fail
    loudly here rather than showing two payoff stories on two screens."""
    from unittest.mock import patch

    from services.consistency import Report, check_windfall_agrees_with_debt

    h = _household(db_session)
    _loan(db_session, h, "A", 500_000, 12.0)
    _loan(db_session, h, "B", 300_000, 8.0)

    real = __import__("api.windfall", fromlist=["compute_windfall"]).compute_windfall

    def drop_one(db, hid, amount=None):
        out = real(db, hid, amount)
        out["prepay"]["considered"] = out["prepay"]["considered"][:1]
        return out

    r = Report()
    with patch("api.windfall.compute_windfall", drop_one):
        check_windfall_agrees_with_debt(db_session, h.id, r)
    f = next(x for x in r.findings if x.check == "windfall_matches_debt")
    assert f.severity == "error", f.detail


# ── the horizon: chosen, not inherited from whichever loan runs longest ──────

def test_the_horizon_is_a_stated_default_not_the_longest_loans_remaining_life(db_session):
    """It used to be `max(remaining life)`. One interest-only gold loan running 384 months therefore
    projected equity 32 years forward and printed a crore next to a prepayment figure — arithmetically
    fine, and meaningless as a comparison. The default is now STATED, so it means the same thing for
    every household regardless of which loan they happen to hold."""
    from api.windfall import DEFAULT_HORIZON_MONTHS

    h = _household(db_session)
    _loan(db_session, h, "Very long", 2_000_000, 8.0, tenure=360)

    out = _win(db_session, h, 100_000)
    assert out["horizon_months"] == DEFAULT_HORIZON_MONTHS
    assert out["horizon_source"] == "default"
    # The debt's own remaining life is still published — it is just no longer the projection window.
    assert out["prepay"]["horizon_months_before"] > DEFAULT_HORIZON_MONTHS
    assert out["horizon_months"] != out["prepay"]["horizon_months_before"]
    assert "stated" in out["horizon_basis"].lower()


def test_the_household_can_choose_the_horizon(db_session):
    h = _household(db_session)
    _loan(db_session, h, "Long", 2_000_000, 8.0, tenure=360)

    out = _win(db_session, h, 100_000, horizon_months=60)
    assert out["horizon_months"] == 60
    assert out["horizon_source"] == "chosen"
    assert out["invest"]["horizon_months"] == 60
    assert out["prepay"]["horizon_months"] == 60
    assert "60" in out["invest"]["method"]


def test_both_sides_move_when_the_horizon_moves(db_session):
    """The prepay side is not horizon-free either: interest avoided is counted INSIDE the window, so
    a short window cannot be shown next to a long projection."""
    h = _household(db_session)
    _loan(db_session, h, "Long", 2_000_000, 8.0, tenure=360)

    short = _win(db_session, h, 300_000, horizon_months=36)
    long = _win(db_session, h, 300_000, horizon_months=240)

    assert short["invest"]["range"]["high_value_inr"] < long["invest"]["range"]["high_value_inr"]
    assert 0 < short["prepay"]["interest_avoided_inr"] < long["prepay"]["interest_avoided_inr"]
    # And the full-term figure is still published, so nothing is hidden by narrowing the window.
    assert short["prepay"]["interest_avoided_full_term_inr"] == pytest.approx(
        long["prepay"]["interest_avoided_full_term_inr"], abs=1.0)
    assert (short["prepay"]["interest_avoided_full_term_inr"]
            >= short["prepay"]["interest_avoided_inr"])


def test_the_choices_are_published_so_no_screen_has_to_invent_them(db_session):
    from api.windfall import DEFAULT_HORIZON_MONTHS

    h = _household(db_session)
    out = _win(db_session, h, 100_000)
    choices = out["horizon_choices"]
    assert len(choices) >= 3
    months = [c["months"] for c in choices]
    assert months == sorted(months)
    assert DEFAULT_HORIZON_MONTHS in months
    assert all(c["label"] for c in choices)


def test_an_impossible_horizon_is_refused_rather_than_clamped(db_session):
    from fastapi import HTTPException

    h = _household(db_session)
    for bad in (0, -12, 361, "ten years"):
        with pytest.raises(HTTPException) as e:
            _win(db_session, h, 100_000, horizon_months=bad)
        assert e.value.status_code == 400, bad


def test_the_route_takes_the_chosen_horizon(db_session, client_for):
    h = _household(db_session)
    _loan(db_session, h, "Only", 500_000, 10.0)
    body = client_for(h).get("/api/windfall?amount=100000&horizon_months=36").json()
    assert body["horizon_months"] == 36
    assert body["horizon_source"] == "chosen"
    assert body["invest"]["horizon_months"] == 36


# ── the measured anchor a human types in ─────────────────────────────────────

def _entered(db, h, rate=10.4, scope="every equity holding at the broker", as_of=None, ago=3):
    """Record a return the household measured somewhere Artha cannot see."""
    from api.windfall import set_measured_return
    return set_measured_return(
        {"rate_pct": rate, "scope": scope,
         "as_of": (as_of or (date.today() - timedelta(days=ago))).isoformat()},
        db=db, household=h)


def test_a_return_measured_elsewhere_becomes_the_observed_anchor(db_session):
    """All seven funds were re-anchored to today against the broker, so there is no in-app history to
    derive an XIRR from. The real figure exists — at the broker. This is how it gets in."""
    h = _household(db_session)
    _entered(db_session, h, rate=10.43)

    out = _win(db_session, h, 100_000)
    observed = [s for s in out["invest"]["scenarios"] if s["source"] == "observed"]
    assert len(observed) == 1
    assert observed[0]["rate_pct"] == pytest.approx(10.4, abs=0.05)
    assert out["invest"]["realised_xirr_unavailable_why"] is None


def test_a_hand_entered_return_says_it_was_measured_ELSEWHERE(db_session):
    """It is measured, but measured by the broker and typed in by a human. Not derived by this app,
    and not an assumption either — so it is neither 'yours, calculated' nor part of the ladder."""
    h = _household(db_session)
    _entered(db_session, h, scope="every equity holding at the broker")

    inv = _win(db_session, h, 100_000)["invest"]
    s = next(x for x in inv["scenarios"] if x["source"] == "observed")
    assert s["observed_from"] == "entered_by_hand"
    assert "hand" in s["assumption"].lower()
    assert "every equity holding at the broker" in s["assumption"]
    # It must not be laundered into the assumed ladder, and must not claim Artha computed it.
    assert s["source"] != "assumed"
    assert "calculated" not in s["label"].lower()
    assert all(x["observed_from"] is None for x in inv["scenarios"] if x["source"] == "assumed")


def test_a_hand_entered_return_records_when_and_what_it_refers_to(db_session):
    """A hand-entered rate goes stale silently, and that is the exact failure this feature exists to
    end. So it carries the date it measures to, the moment a human typed it, and its age."""
    h = _household(db_session)
    _entered(db_session, h, ago=10, scope="broker console, equity only")

    m = _win(db_session, h, 100_000)["invest"]["measured_return"]
    assert m["scope"] == "broker console, equity only"
    assert m["as_of"] == (date.today() - timedelta(days=10)).isoformat()
    assert m["entered_at"]
    assert m["as_of_days_ago"] == 10
    assert m["stale"] is False
    assert m["stale_after_days"] > 0
    assert m["in_use"] is True
    assert m["superseded_by_derived"] is False
    assert m["why_not_derived"]


def test_an_old_hand_entered_return_is_flagged_stale_and_still_shown(db_session):
    """Hiding it would silently drop the only measured figure the household has. Flagging it is the
    honest move: still used, visibly old."""
    from api.windfall import STALE_AFTER_DAYS

    h = _household(db_session)
    _entered(db_session, h, ago=STALE_AFTER_DAYS + 30)

    inv = _win(db_session, h, 100_000)["invest"]
    assert inv["measured_return"]["stale"] is True
    s = next(x for x in inv["scenarios"] if x["source"] == "observed")
    assert s["stale"] is True
    assert "stale" in s["assumption"].lower() or "old" in s["assumption"].lower()


def test_a_derived_xirr_wins_over_the_hand_entered_one(db_session):
    """Once redemption history exists the app can measure this itself, and a figure it can check
    beats one it cannot. The typed one stays visible, marked superseded — never silently ignored."""
    h = _household(db_session)
    now = datetime.now(timezone.utc).isoformat()
    db_session.add(RecurringInvestment(
        household_id=h.id, kind="sip", name="Fund", opening_balance=100_000.0,
        as_of_date=date.today() - timedelta(days=400), monthly_contribution=0.0,
        annual_rate_pct=12.0, compounding="monthly",
        market={"scheme_code": "1", "units": 1000.0, "last_nav": 150.0,
                "nav_date": "2026-08-18", "last_nav_at": now}))
    db_session.commit()
    _entered(db_session, h, rate=10.4)

    inv = _win(db_session, h, 100_000)["invest"]
    observed = [s for s in inv["scenarios"] if s["source"] == "observed"]
    assert len(observed) == 1
    assert observed[0]["observed_from"] == "derived_in_app"
    assert observed[0]["rate_pct"] != pytest.approx(10.4, abs=0.05)
    assert inv["measured_return"]["superseded_by_derived"] is True
    assert inv["measured_return"]["in_use"] is False


def test_with_nothing_entered_nothing_is_substituted(db_session):
    """Unchanged behaviour: no observed anchor, and the reason published. Never a default."""
    h = _household(db_session)
    inv = _win(db_session, h, 100_000)["invest"]
    assert [s for s in inv["scenarios"] if s["source"] == "observed"] == []
    assert inv["measured_return"] is None
    assert inv["realised_xirr_unavailable_why"]


def test_a_measured_return_must_say_what_it_refers_to_and_when(db_session):
    """An undated, unscoped rate is the bare rate this whole app refuses to render."""
    from fastapi import HTTPException

    h = _household(db_session)
    bad = [
        {"rate_pct": 10.4, "scope": "  ", "as_of": date.today().isoformat()},
        {"rate_pct": 10.4, "scope": "broker"},
        {"rate_pct": 10.4, "scope": "broker", "as_of": "not-a-date"},
        {"rate_pct": 10.4, "scope": "broker",
         "as_of": (date.today() + timedelta(days=1)).isoformat()},
        {"rate_pct": "ten", "scope": "broker", "as_of": date.today().isoformat()},
        {"rate_pct": 900.0, "scope": "broker", "as_of": date.today().isoformat()},
        {"scope": "broker", "as_of": date.today().isoformat()},
    ]
    for payload in bad:
        with pytest.raises(HTTPException) as e:
            from api.windfall import set_measured_return
            set_measured_return(payload, db=db_session, household=h)
        assert e.value.status_code == 400, payload


def test_a_negative_measured_return_is_accepted_because_it_can_be_true(db_session):
    h = _household(db_session)
    _entered(db_session, h, rate=-4.2)
    s = next(x for x in _win(db_session, h, 100_000)["invest"]["scenarios"]
             if x["source"] == "observed")
    assert s["rate_pct"] == pytest.approx(-4.2, abs=0.05)


def test_the_entry_round_trips_through_the_route_and_can_be_removed(db_session, client_for):
    c = client_for(h := _household(db_session))
    assert c.get("/api/windfall/measured-return").json()["measured_return"] is None

    put = c.put("/api/windfall/measured-return", json={
        "rate_pct": 10.43, "scope": "every equity holding at the broker",
        "as_of": date.today().isoformat()})
    assert put.status_code == 200, put.text
    assert put.json()["measured_return"]["rate_pct"] == pytest.approx(10.43, abs=0.01)

    body = c.get("/api/windfall?amount=100000").json()
    assert any(s["observed_from"] == "entered_by_hand" for s in body["invest"]["scenarios"])

    assert c.delete("/api/windfall/measured-return").json()["measured_return"] is None
    body = c.get("/api/windfall?amount=100000").json()
    assert [s for s in body["invest"]["scenarios"] if s["source"] == "observed"] == []
    assert body["invest"]["realised_xirr_unavailable_why"]
    assert _household is not None and h.id is not None


# ── two streams, not one pot ─────────────────────────────────────────────────
#
# The surplus and the windfall were never the same rupees. The monthly surplus is ALREADY going at
# the debt — api/debt has a strategy for it and publishes the dates. The windfall is the only thing
# actually being decided. Measuring it against a world where the surplus does not exist credits the
# windfall with interest the surplus was going to avoid anyway.

def _with_surplus(db, h, income=300_000.0, fixed=50_000.0):
    db.add(CashflowLine(household_id=h.id, kind="income", label="Salary",
                        amount_inr=income, active=True))
    db.add(CashflowLine(household_id=h.id, kind="fixed", label="Rent",
                        amount_inr=fixed, active=True))
    db.commit()


def test_the_prepay_baseline_is_the_payoff_plan_already_running(db_session):
    """Not contracted-EMIs-to-term. The baseline is the plan api/debt already publishes, with the
    household's monthly surplus flowing — and it is api/debt's own simulation, not a copy."""
    h = _household(db_session)
    _loan(db_session, h, "Dear", 800_000, 16.0, tenure=84)
    _loan(db_session, h, "Cheap", 600_000, 9.0, tenure=120)
    _with_surplus(db_session, h)

    from api.debt import compute_debt
    strat = compute_debt(db_session, h.id)["strategies"]
    assert strat["plan_extra"] > 0, "fixture produced no surplus"
    ava = next(o for o in strat["options"] if o["key"] == "avalanche")

    p = _win(db_session, h, 500_000)["prepay"]
    assert p["baseline"]["months"] == ava["months"]
    assert p["baseline"]["interest_full_term_inr"] == pytest.approx(ava["total_interest"], abs=1.0)
    assert p["baseline"]["with_surplus_inr"] == pytest.approx(strat["plan_extra"], abs=0.01)


def test_the_surplus_is_not_credited_to_the_windfall(db_session):
    """The headline used to be measured against a no-surplus world, so most of the interest it claimed
    to avoid was already being avoided by the surplus. It must be strictly smaller than that."""
    from api.debt import compute_debt, payoff_simulation

    h = _household(db_session)
    _loan(db_session, h, "Dear", 800_000, 16.0, tenure=84)
    _loan(db_session, h, "Cheap", 600_000, 9.0, tenure=120)
    _with_surplus(db_session, h)

    attackable = [ln for ln in compute_debt(db_session, h.id)["loans"]
                  if not ln["closed"] and ln["balance_inr"] > 0]
    no_surplus = (payoff_simulation(attackable, 0.0)["total_interest"]
                  - payoff_simulation(attackable, 0.0, lump=500_000)["total_interest"])

    p = _win(db_session, h, 500_000)["prepay"]
    assert p["interest_avoided_full_term_inr"] > 0
    assert p["interest_avoided_full_term_inr"] < no_surplus


def test_the_surplus_it_assumed_is_published_with_the_choice_stated(db_session):
    """A reader must be able to see which surplus produced the numbers, and why that one."""
    h = _household(db_session)
    _loan(db_session, h, "Dear", 800_000, 16.0, tenure=84)
    _with_surplus(db_session, h)

    from api.debt import compute_debt
    strat = compute_debt(db_session, h.id)["strategies"]

    s = _win(db_session, h, 300_000)["prepay"]["surplus"]
    assert s["assumed_per_month_inr"] == pytest.approx(strat["plan_extra"], abs=0.01)
    assert s["field"] == "strategies.plan_extra"
    assert s["surplus_inr"] == pytest.approx(strat["surplus_context"]["surplus"], abs=0.01)
    assert s["actionable_inr"] == pytest.approx(strat["surplus_context"]["actionable"], abs=0.01)
    assert "actionable" in s["why_this_one"].lower()
    assert s["data_ok"] is True


def test_both_arms_have_the_surplus_flowing(db_session):
    """Investing the windfall does not stop the surplus clearing the debt. The invest side says so,
    on the same schedule the prepay side calls its baseline."""
    h = _household(db_session)
    _loan(db_session, h, "Dear", 800_000, 16.0, tenure=84)
    _with_surplus(db_session, h)

    out = _win(db_session, h, 300_000)
    assert out["invest"]["while_invested"]["debt_months"] == out["prepay"]["baseline"]["months"]
    assert out["invest"]["while_invested"]["surplus_per_month_inr"] == pytest.approx(
        out["prepay"]["surplus"]["assumed_per_month_inr"], abs=0.01)
    assert "surplus" in out["invest"]["while_invested"]["note"].lower()


def test_the_windfall_arm_is_the_same_plan_plus_the_lump(db_session):
    """Not a second amortisation: the with-windfall arm is api/debt's own simulation run with the same
    surplus and the lump paid in today."""
    from api.debt import compute_debt, payoff_simulation

    h = _household(db_session)
    _loan(db_session, h, "Dear", 800_000, 16.0, tenure=84)
    _loan(db_session, h, "Cheap", 600_000, 9.0, tenure=120)
    _with_surplus(db_session, h)

    debt = compute_debt(db_session, h.id)
    extra = debt["strategies"]["plan_extra"]
    attackable = [ln for ln in debt["loans"] if not ln["closed"] and ln["balance_inr"] > 0]
    expect = payoff_simulation(attackable, extra, lump=400_000)

    p = _win(db_session, h, 400_000)["prepay"]
    assert p["with_windfall"]["months"] == expect["months"]
    assert p["with_windfall"]["interest_full_term_inr"] == pytest.approx(
        expect["total_interest"], abs=1.0)
    assert p["months_removed"] == p["baseline"]["months"] - p["with_windfall"]["months"]
