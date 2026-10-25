"""Cross-module invariants: the checks that catch a tab disagreeing with another tab.

Every module here is correct in isolation and they still contradicted each other, because each was
built in its own tab and nothing asserted they agree. Two live examples:

  `leftover` was read as spare cash when it is the variable-spend BUDGET, and redefining it to
  "fix" that broke the main screen: cockpit's safe_to_spend already subtracted spend from it, so the
  screen double-subtracted and showed -22,500. The field was right; the reader was wrong. Both
  meanings are now published separately (leftover, spare) and BOTH sides are asserted.

  networth keeps TWO separate asset queries (compute_assets and compute_networth). Disposal
  receivables were added to one of them, so the assets endpoint and the net-worth endpoint disagreed
  by Rs 6,50,000 until the second was patched too.

The pattern in both: a value has an owner module, a second module needs it, and instead of asking
the owner it recomputes, guesses, or omits. So each check below names the OWNER and compares every
other reading against it. A check earns its place by having caught a real defect, not by being
theoretically nice.

Findings are advisory. This never raises: a broken invariant must not take down the app, it must be
visible. Severity is "error" when the number is materially wrong today and "warn" when it is a
latent trap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

TOLERANCE_INR = 1.0          # rounding only; anything above this is a real disagreement
# api/debt caps a forward schedule at this many rows, so a longer loan is truncated by design and a
# length mismatch there says nothing about the loan.
FORWARD_SCHEDULE_CAP = 600


@dataclass
class Finding:
    check: str
    severity: str                 # error | warn | ok
    owner: str                    # the module that owns the number
    detail: str
    expected: float | None = None
    actual: float | None = None
    fix: str | None = None

    @property
    def gap(self) -> float | None:
        if self.expected is None or self.actual is None:
            return None
        return round(self.actual - self.expected, 2)


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def as_dict(self) -> dict:
        return {
            "ok": not self.errors,
            "error_count": len(self.errors),
            "warn_count": len([f for f in self.findings if f.severity == "warn"]),
            "findings": [
                {"check": f.check, "severity": f.severity, "owner": f.owner,
                 "detail": f.detail, "expected": f.expected, "actual": f.actual,
                 "gap": f.gap, "fix": f.fix}
                for f in self.findings
            ],
        }


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= TOLERANCE_INR


# ---------------------------------------------------------------- individual checks

def check_leftover_closes_the_waterfall(db: Session, hid: int, report: Report) -> None:
    """cashflow.leftover must remain the income-waterfall residual, and `spare` must be net of spend.

    Both cockpit's income breakdown and MonthlyFlow render leftover as the closing row of
    income minus every committed bucket. If it stops being the residual the rows no longer sum to
    income, and cockpit's safe_to_spend (which subtracts spend from it) double-subtracts. That put
    -22,500 on the main screen."""
    from api.budget import compute_budget
    from api.cashflow import compute_cashflow

    cf = compute_cashflow(db, hid)
    residual = round(cf["income"] - cf["emi"] - cf["fixed"] - cf["saving"]
                     - cf["sinking"] - cf["insurance"], 2)
    if not _close(cf["leftover"], residual):
        report.add(Finding(
            "leftover_closes_waterfall", "error", "api/cashflow",
            "leftover is no longer the income residual, so the cockpit breakdown stops summing to "
            "income and safe_to_spend double-subtracts spend",
            expected=residual, actual=cf["leftover"],
            fix="leftover must stay income − emi − fixed − saving − sinking − insurance; "
                "put anything net of spend in `spare`"))
        return

    owner_spend = float(compute_budget(db=db, household_id=hid).get("net_spent_inr") or 0.0)
    if not _close(cf.get("spent"), owner_spend):
        report.add(Finding(
            "leftover_closes_waterfall", "error", "api/budget",
            "cashflow.spent disagrees with the budget module, which owns actual spend",
            expected=owner_spend, actual=cf.get("spent"),
            fix="read net_spent_inr from compute_budget rather than recomputing"))
    elif not _close(cf.get("spare"), cf["leftover"] - cf["spent"]):
        report.add(Finding(
            "leftover_closes_waterfall", "error", "api/cashflow",
            "spare is not leftover minus spent",
            expected=round(cf["leftover"] - cf["spent"], 2), actual=cf.get("spare")))
    else:
        report.add(Finding("leftover_closes_waterfall", "ok", "api/cashflow",
                           f"leftover {cf['leftover']:,.0f} closes the waterfall; "
                           f"spare {cf['spare']:,.0f} = leftover − spent {cf['spent']:,.0f}"))


def check_cockpit_does_not_double_subtract(db: Session, hid: int, report: Report) -> None:
    """cockpit.safe_to_spend must equal leftover minus spend exactly once.

    This is the check that would have caught the -22,500. It exists because the previous check
    validated cashflow in isolation and never looked at what consumes it — which is the same
    mistake, one layer up."""
    from api.cashflow import compute_cashflow
    from api.cockpit import get_cockpit
    from models.tenant import Household

    cf = compute_cashflow(db, hid)
    household = db.query(Household).filter(Household.id == hid).first()
    if household is None:
        report.add(Finding("cockpit_double_subtract", "warn", "api/cockpit",
                           f"no household {hid} to render the cockpit for"))
        return
    # The route function IS the contract the screen sees; checking anything less would miss exactly
    # the layer where this broke.
    ck = get_cockpit(mode="cycle", db=db, household=household)
    sts = ck.get("safe_to_spend")
    cycle_spent = (ck.get("cycle") or {}).get("spent")
    if sts is None or cycle_spent is None:
        report.add(Finding("cockpit_double_subtract", "warn", "api/cockpit",
                           "cockpit exposes no safe_to_spend/cycle.spent to verify"))
        return
    expected = round(cf["leftover"] - cycle_spent, 2)
    if not _close(sts, expected):
        report.add(Finding(
            "cockpit_double_subtract", "error", "api/cockpit",
            "safe_to_spend is not leftover minus this cycle's spend — spend is being subtracted "
            "twice, or leftover changed meaning underneath it",
            expected=expected, actual=sts,
            fix="safe_to_spend = cashflow.leftover − cycle spend, subtracted exactly once"))
    else:
        report.add(Finding("cockpit_double_subtract", "ok", "api/cockpit",
                           f"safe_to_spend {sts:,.0f} subtracts spend exactly once"))


def check_networth_endpoints_agree(db: Session, hid: int, report: Report) -> None:
    """networth.py has TWO asset queries. Anything added to one must be added to the other.

    Caught exactly this: disposal receivables went into compute_assets only, so the two endpoints
    disagreed by the receivable amount. The duplication is the hazard; until it is collapsed into
    one query this check stands in for it."""
    from api.networth import compute_assets, compute_networth

    a = compute_assets(db, hid)["total_inr"]
    n = compute_networth(db=db, household_id=hid)
    cats = {c["category"]: c["value"] for c in n.get("categories", [])}
    # Asset-table categories only; the rest of net worth comes from recurring/sinking.
    asset_side = sum(v for k, v in cats.items()
                     if k in {"Cash & Bank", "Market", "Gold", "Vehicles", "Property"})
    # Market includes SIP holdings from the recurring engine, so an exact match is not expected.
    # A receivable missing from one side shows up as the asset endpoint exceeding the net-worth side.
    if a - asset_side > TOLERANCE_INR:
        report.add(Finding(
            "networth_endpoints_agree", "error", "api/networth",
            "compute_assets includes value that compute_networth does not; the two asset queries "
            "have drifted",
            expected=a, actual=asset_side,
            fix="both queries must apply the same includes (disposal receivables, active filter)"))
    else:
        report.add(Finding("networth_endpoints_agree", "ok", "api/networth",
                           "both asset queries agree"))


def check_emi_single_source(db: Session, hid: int, report: Report) -> None:
    """Everything quoting an EMI must get it from services/loan_obligation via the loans table.

    A flexi or moratorium loan costs a different amount under a naive formula, which is why the
    cash-flow tab and the debt tab once diverged."""
    from api.cashflow import compute_cashflow
    from api.debt import compute_debt

    cf_emi = compute_cashflow(db, hid)["emi"]
    debt = compute_debt(db, hid)
    debt_emi = debt.get("total_emi") or debt.get("monthly_emi")
    if debt_emi is None:
        report.add(Finding("emi_single_source", "warn", "api/debt",
                           "debt module exposes no total EMI to compare against"))
        return
    if not _close(cf_emi, float(debt_emi)):
        report.add(Finding(
            "emi_single_source", "error", "services/loan_obligation",
            "the cash-flow tab and the debt tab quote different EMIs",
            expected=float(debt_emi), actual=cf_emi,
            fix="both must call loan_obligation.monthly_emi, never a local formula"))
    else:
        report.add(Finding("emi_single_source", "ok", "services/loan_obligation",
                           f"EMI {cf_emi:,.0f} agrees across cash-flow and debt"))


def check_transactions_reconcile_with_ledgers(db: Session, hid: int, report: Report) -> None:
    """Raw transaction sums must never be used as a spend figure.

    Across this household's accounts the raw debit total runs several times income, because a card
    purchase and the bank debit settling it both appear, alongside inter-account transfers. Only
    api/budget filters those correctly. This check exists so nobody is tempted again."""
    from api.cashflow import compute_cashflow
    from models.transaction import Transaction as T
    from sqlalchemy import func

    months = [m[0] for m in db.query(T.stmt_month).filter(T.household_id == hid)
              .distinct().order_by(T.stmt_month.desc()).limit(6).all()]
    if not months:
        report.add(Finding("transactions_reconcile", "warn", "api/budget",
                           "no statement months parsed yet"))
        return
    raw = float(db.query(func.sum(T.amount)).filter(
        T.household_id == hid, T.direction == "debit",
        T.stmt_month.in_(months)).scalar() or 0.0)
    # The same rows the analytics and budget modules count: real discretionary spend only, with
    # merged duplicates and archived rows excluded.
    real = float(db.query(func.sum(T.amount)).filter(
        T.household_id == hid, T.direction == "debit", T.txn_type == "SPEND",
        T.dup_of_id.is_(None), T.archived.is_(False),
        T.stmt_month.in_(months)).scalar() or 0.0)
    share = (real / raw * 100.0) if raw else 100.0
    income = compute_cashflow(db, hid)["income"]
    # Reporting the raw total as if it meant something was itself misleading — it is mostly
    # transfers and card settlements. What matters is that summing raw debits overstates spend by
    # a large multiple, so nobody should ever do it.
    if raw / len(months) > income:
        report.add(Finding(
            "transactions_reconcile", "warn", "api/budget",
            f"only {share:.0f}% of debits are real spend ({real/len(months):,.0f}/mo of "
            f"{raw/len(months):,.0f}/mo) — the rest is transfers, card settlements, EMIs, merged "
            f"duplicates and archived rows",
            expected=round(real / len(months), 2), actual=round(raw / len(months), 2),
            fix="read spend from compute_budget (net_spent_inr), or filter txn_type=SPEND with "
                "dup_of_id IS NULL and archived IS FALSE — never a bare SUM(amount)"))
    else:
        report.add(Finding("transactions_reconcile", "ok", "api/budget",
                           f"real spend {real/len(months):,.0f}/mo, {share:.0f}% of debits"))


def check_recurring_classification(db: Session, hid: int, report: Report) -> None:
    """Money earmarked for a known future outflow is a sinking fund, not an investment.

    The household's donation RDs sit in recurring_investments, so they inflate the invested figure
    and distort the debt/equity ratio for money that never becomes wealth."""
    from models.recurring import RecurringInvestment

    rows = db.query(RecurringInvestment).filter(
        RecurringInvestment.household_id == hid,
        RecurringInvestment.active.is_(True)).all()
    suspects = [r for r in rows
                if (r.kind or "").lower() == "rd" and (r.opening_balance or 0) == 0
                and (r.purpose or "wealth") == "wealth"]
    if suspects:
        names = ", ".join(f"{r.name} ({r.monthly_contribution or 0:,.0f}/mo)" for r in suspects)
        report.add(Finding(
            "recurring_classification", "warn", "api/recurring",
            f"RD with no opening balance and purpose='wealth' — if the money is already spoken for "
            f"it is not wealth: {names}",
            fix="set purpose='donation' so it stays in takehome_outflow but leaves wealth_outflow"))
    else:
        tagged = [r for r in rows if (r.purpose or "wealth") != "wealth"]
        report.add(Finding("recurring_classification", "ok", "api/recurring",
                           f"every recurring row carries a purpose ({len(tagged)} non-wealth)"))


def check_gold_loans_not_amortised(db: Session, hid: int, report: Report) -> None:
    """A gold loan must never be run through an amortisation formula.

    loan_type='gold' is an interest-only bullet account with a payment ledger: principal only drops
    when a payment exceeds accrued interest, and `expected_monthly_payment` is a projection input,
    NOT an EMI. Both the model and loan_math say so. Treating it as an EMI produces a payoff date
    that cannot happen, which is exactly the mistake that produced a fictional 58-month plan.

    So this does not flag the interest-only shape (that is correct by design). It flags a gold loan
    whose inputs are missing, since that is when a caller silently falls back to EMI maths."""
    from models.household import Loan

    q = db.query(Loan).filter(Loan.household_id == hid, Loan.closed.is_(False))
    bad, gold = [], 0
    for ln in q.all():
        if (ln.loan_type or "").lower() != "gold":
            continue
        gold += 1
        if not ln.expected_monthly_payment:
            bad.append(f"{ln.name}: no expected_monthly_payment, so any leftover projection is blind")
        if ln.emi_override:
            bad.append(f"{ln.name}: has emi_override set, which invites amortisation maths on a "
                       f"bullet account")
    if bad:
        report.add(Finding(
            "gold_loans_not_amortised", "warn", "services/loan_math",
            "gold-loan inputs are incomplete: " + "; ".join(bad),
            fix="gold loans must be valued through loan_math.compute_gold_state, never an EMI loop"))
    else:
        report.add(Finding("gold_loans_not_amortised", "ok", "services/loan_math",
                           f"{gold} gold loan(s) carry the inputs compute_gold_state needs"))


def check_loans_are_self_consistent(db: Session, hid: int, report: Report) -> None:
    """Every active loan must tell ONE story: a payment that eventually clears it, a forward schedule
    that exists while a balance does, a schedule as long as months_remaining claims, and an EMI that is
    the formula's own answer on the loan's stated inputs.

    Each of those is a number the Debt screen shows next to the others, so a disagreement is two payoff
    dates on one page. This ran once as a throwaway audit; it belongs here because a one-off script
    proves the loans were consistent on the day it ran and nothing more.

    No loan is named in the logic. Two exclusions carry the whole difference between a check and a
    false-alarm generator:

      A gold loan is an interest-only bullet account BY DESIGN — `expected_monthly_payment` is a nominal
      outflow, not an EMI, and principal only moves when a payment beats the accrued interest. It is
      therefore never judged on covering interest; doing that maths on one is what produced a fictional
      58-month debt-free date.

      A loan in moratorium pays interest only TODAY and steps up to `post_moratorium_emi` later. Judged
      on today's payment every education loan looks like it never amortises, which is why the schedule
      itself is built from steps rather than one flat figure.
    """
    from api.debt import get_loan_detail
    from models.household import Loan
    from models.tenant import Household
    from services import loan_math

    household = db.query(Household).filter(Household.id == hid).first()
    if household is None:
        report.add(Finding("loans_self_consistent", "warn", "api/debt",
                           f"no household {hid} to read loans for"))
        return

    loans = db.query(Loan).filter(Loan.household_id == hid, Loan.closed.is_(False)).all()
    errors: list[str] = []
    warns: list[str] = []
    for loan in loans:
        try:
            # The route function is the contract the Debt screen sees; forward_schedule exists nowhere
            # else, and rebuilding it here would recompute a number this module does not own.
            v = get_loan_detail(loan.id, db=db, household=household)
        except Exception as exc:  # noqa: BLE001 — one unreadable loan must not hide the rest
            warns.append(f"{getattr(loan, 'name', loan.id)}: could not be read ({exc})")
            continue

        name = v.get("name") or f"loan {loan.id}"
        kind = (v.get("loan_type") or "standard").lower()
        rate = float(v.get("rate_pct") or 0.0)
        # Same inputs api/debt feeds its own schedule: a gold loan amortises its PRINCIPAL against the
        # nominal payment, and balance_inr there carries accrued interest that no payment amortises.
        balance = float((v.get("principal_outstanding") if kind == "gold"
                         else v.get("balance_inr")) or 0.0)
        payment = float((v.get("expected_monthly_payment") if kind == "gold"
                         else v.get("emi_inr")) or 0.0)
        step_up = v.get("post_moratorium_emi")
        eventual = (float(step_up) if v.get("phase") == "moratorium" and step_up else payment)
        interest = balance * rate / 1200.0
        amortises = eventual > interest

        if kind == "standard" and not getattr(loan, "emi_override", None):
            formula = loan_math.emi(loan.original_principal, loan.annual_rate_pct, loan.tenure_months)
            if abs(float(v.get("emi_inr") or 0.0) - formula) > TOLERANCE_INR:
                errors.append(f"{name}: EMI {float(v.get('emi_inr') or 0.0):,.0f} is not the formula's "
                              f"{formula:,.0f} on its own principal, rate and tenure")

        if balance <= 0:
            continue

        months_passed = int(v.get("months_passed") or 0)
        tenure = int(v.get("tenure_months") or 0)
        # Only amortising loans contradict themselves here. A gold loan's tenure is a renewal date on a
        # bullet account, so running past it while owing is routine, not a disagreement.
        if kind != "gold" and tenure and months_passed > tenure:
            warns.append(f"{name}: {months_passed} months elapsed against a {tenure}-month tenure with "
                         f"{balance:,.0f} still owed — the stated tenure no longer describes this loan")

        if kind != "gold" and not amortises:
            errors.append(f"{name}: pays {eventual:,.0f}/mo against {interest:,.0f}/mo of interest and "
                          f"no later step covers it — the balance never falls, so its closure date is "
                          f"fiction")
            continue

        if not amortises:
            continue

        sched = v.get("forward_schedule") or []
        left = int(v.get("months_remaining") or 0)
        if not sched:
            errors.append(f"{name}: {balance:,.0f} outstanding but no forward schedule, so the screen "
                          f"cannot say which month it can be closed in or what that costs")
        elif len(sched) >= FORWARD_SCHEDULE_CAP and left >= FORWARD_SCHEDULE_CAP:
            warns.append(f"{name}: forward schedule stops at the {FORWARD_SCHEDULE_CAP}-row cap while "
                         f"{left} months remain")
        elif abs(len(sched) - left) > 1:
            errors.append(f"{name}: forward schedule runs {len(sched)} months but months_remaining says "
                          f"{left} — the same screen carries two payoff dates")

    if errors:
        report.add(Finding(
            "loans_self_consistent", "error", "api/debt",
            "; ".join(errors + warns),
            fix="a loan's EMI, balance, months_remaining and forward schedule all come from "
                "loan_math via loan_obligation — fix the inputs, never the derived figure"))
    elif warns:
        report.add(Finding("loans_self_consistent", "warn", "api/debt", "; ".join(warns)))
    else:
        report.add(Finding("loans_self_consistent", "ok", "api/debt",
                           f"{len(loans)} active loan(s): every payment clears its interest, and each "
                           f"schedule agrees with its own months_remaining"))


def check_analytics_agrees_with_plan(db: Session, hid: int, report: Report) -> None:
    """Every Analytics cycle column must equal Plan's spend for the window in its own label.

    These were built in separate tabs and drifted: Analytics bucketed each transaction by its own
    CARD's cycle while labelling the column with the HOUSEHOLD window, so an Axis purchase on 15 Jul
    counted as "one cycle ago" and landed in a column labelled 13 Jun - 12 Jul. Only the current
    column coincided, which is exactly what makes it hard to spot -- the number you check first is
    the one that agrees. The gap reached Rs 24,000 on a single column.

    Checks the labelled window, not the mode, so any future re-bucketing that breaks the label fails
    here regardless of how it was implemented.
    """
    from api.analytics import analytics_cycle_matrix
    from api.budget import compute_budget
    from models.tenant import Household
    from services.consistency import Finding  # noqa: F401  (re-exported for clarity)

    hh = db.query(Household).filter(Household.id == hid).first()
    if hh is None:
        return
    try:
        m = analytics_cycle_matrix(count=4, db=db, household=hh)
    except Exception as exc:  # noqa: BLE001 — a broken collaborator must not hide the other checks
        report.add(Finding("analytics_matches_plan", "warn", "api/analytics",
                           f"could not run: {type(exc).__name__}"))
        return

    worst = None
    for c in m.get("cycles", []):
        # Analytics publishes GROSS spend with refunds in a separate series; Plan publishes NET.
        # Comparing the two directly reports the refunds as a discrepancy — it read as a Rs 24,000
        # bug on one column when the two modules agreed exactly.
        got = (float(m["grand_total_by_cycle"].get(c["key"], 0.0))
               - float(m.get("refunds_by_cycle", {}).get(c["key"], 0.0)))
        want = float(compute_budget(db=db, household_id=hid, anchor=c["end"])["net_spent_inr"])
        if abs(got - want) > 1.0 and (worst is None or abs(got - want) > abs(worst[3] - worst[2])):
            worst = (c["key"], c["start"], want, got)
    if worst:
        k, start, want, got = worst
        report.add(Finding("analytics_matches_plan", "error", "api/analytics",
                           f"column {k} ({start} onward) nets to {got:,.0f} but Plan says "
                           f"{want:,.0f} for the same window — gap {got - want:,.0f}"))
    else:
        report.add(Finding("analytics_matches_plan", "ok", "api/analytics",
                           f"all {len(m.get('cycles', []))} cycle columns agree with Plan "
                           f"once refunds are netted off"))


def check_no_misparsed_rows(db: Session, hid: int, report: Report) -> None:
    """Catch a parser that shifted columns, by looking at what it wrote rather than how it ran.

    Every failure this session was SILENT. The ICICI savings statement handed to the card parser did
    not raise; it produced confident rows where the running balance became the amount and the real
    amount became the description. Rs 25,000 of spend that never happened, found only because a
    person noticed a budget had flipped.

    A description that is nothing but a number is the signature: descriptions name merchants, and
    "119.00" or "7.00" names nothing. Cheap to check and specific enough not to cry wolf.

    Refusing unknown accounts stops the case we hit. It does NOT stop the same shift on a KNOWN
    account, so this watches the output instead of the input.
    """
    # Matched in Python, not SQL. Postgres '~' would work in production and silently skip every
    # test, since the suite runs on SQLite — a check that cannot be tested is not a check.
    import re

    from models.transaction import Transaction
    numeric = re.compile(r"^[0-9][0-9,]*\.?[0-9]*$")
    rows = [r for r in db.query(Transaction)
            .filter(Transaction.household_id == hid, Transaction.archived.is_(False),
                    Transaction.dup_of_id.is_(None)).all()
            if r.description and numeric.match(r.description.strip())]
    if rows:
        total = sum(r.amount for r in rows)
        worst = max(rows, key=lambda r: r.amount)
        report.add(Finding("no_misparsed_rows", "error", "services/ingest",
                           f"{len(rows)} row(s) worth {total:,.0f} have a NUMBER as their "
                           f"description (e.g. #{worst.id} {worst.amount:,.0f} = "
                           f"{worst.description!r}) — a parser read the wrong column"))
    else:
        report.add(Finding("no_misparsed_rows", "ok", "services/ingest",
                           "no row has a bare number where its merchant should be"))


def check_sync_is_alive(db: Session, hid: int, report: Report) -> None:
    """The sync must have completed recently. Silence is not evidence of health.

    It runs every ten minutes. When it stopped there was no signal: the app kept serving the figures
    it already had, and the symptom was a person buying something and not seeing it appear. A quiet
    week and a dead job look identical from outside, which is why this was found by a human.

    Two hours, not twenty minutes. A machine that slept, or one slow run, must not raise an alarm
    that gets learned as noise — an alert nobody trusts is worse than none.
    """
    from datetime import datetime, timedelta

    from models.sync_run import SyncRun

    last = (db.query(SyncRun)
            .filter(SyncRun.household_id == hid, SyncRun.ok.is_(True))
            .order_by(SyncRun.finished_at.desc().nullslast()).first())
    if last is None or last.finished_at is None:
        report.add(Finding("sync_is_alive", "warn", "scripts/daily_sync",
                           "no completed sync on record yet — the heartbeat starts at the next run"))
        return
    age = datetime.utcnow() - last.finished_at
    if age > timedelta(hours=2):
        hrs = age.total_seconds() / 3600
        report.add(Finding("sync_is_alive", "error", "scripts/daily_sync",
                           f"last completed sync was {hrs:.1f}h ago — nothing has been captured "
                           f"since, and new spending is missing rather than absent"))
    else:
        mins = age.total_seconds() / 60
        report.add(Finding("sync_is_alive", "ok", "scripts/daily_sync",
                           f"last completed sync {mins:.0f} min ago"))


def check_no_card_purchase_typed_as_emi(db: Session, hid: int, report: Report) -> None:
    """A card purchase must not be filed as a loan repayment.

    EMIs are excluded from variable spend, so a misfiled purchase vanishes from the budget without
    vanishing from the ledger — it reconciles perfectly and is simply not counted. A card purchase
    was typed EMI because the classifier rule listed 'ICICI Loan' as a lender and every
    ICICI card alert contains the word ICICI, which is who ISSUED the card.

    A real EMI names the loan: 'EMI', 'Loan', 'Installment'. A row typed EMI whose description names
    none of those is either misfiled or described too poorly to justify leaving out of the budget.
    """
    from models.transaction import Transaction

    # Requires a KNOWN CONSUMER MERCHANT, not merely the absence of the word "loan". The first
    # version flagged any EMI row whose description lacked a loan keyword and caught 26 rows worth
    # several lakh — a builder's construction instalments, and auto-debits described only as "ICICIBANK".
    # Those are uninformative, not misfiled, and an alert that fires on 26 legitimate rows is one
    # you learn to ignore. Reusing dedup's provider table keeps this to the real contradiction: the
    # row resolves to Amazon/Swiggy/Zomato AND is filed as a loan repayment.
    from services.dedup import _provider

    words = ("emi", "loan", "installment", "instalment", "repayment")
    bad = [r for r in db.query(Transaction)
           .filter(Transaction.household_id == hid, Transaction.archived.is_(False),
                   Transaction.dup_of_id.is_(None), Transaction.txn_type == "EMI").all()
           if r.description and not any(w in r.description.lower() for w in words)
           and _provider(r) is not None]
    if bad:
        total = sum(r.amount for r in bad)
        worst = max(bad, key=lambda r: r.amount)
        report.add(Finding("card_purchase_not_emi", "error", "services/ai_categorize",
                           f"{len(bad)} row(s) worth {total:,.0f} are typed EMI but name no loan "
                           f"(e.g. #{worst.id} {worst.amount:,.0f} {worst.description!r}) — "
                           f"excluded from spend without being a repayment"))
    else:
        report.add(Finding("card_purchase_not_emi", "ok", "services/ai_categorize",
                           "every EMI row names an actual loan"))


def check_cycle_straddle_is_declared(db: Session, hid: int, report: Report) -> None:
    """Say plainly how much spend the household cycle does NOT align to.

    One anchor cannot be right for five cards. The household closes on the 12th, which matches the
    ICICI cards; Axis bills on the 21st and HDFC on the 22nd, so their purchases land in a household
    cycle that is not the cycle they will be billed in. That is a real limitation, not a bug, and
    the failure mode is believing a cycle total means 'what I will be billed'.

    Reported as a warning, never an error: there is nothing to fix, only something to know. It turns
    a silent assumption into a stated one.
    """
    from models.account import Account
    from models.transaction import Transaction
    from services.cycle import household_start_day
    from sqlalchemy import func

    hh = household_start_day(db, hid)
    cards = (db.query(Account)
             .filter(Account.household_id == hid, Account.kind == "card",
                     Account.active.is_(True)).all())
    off = [c for c in cards if c.statement_day and abs((c.statement_day + 1) - hh) > 1]
    if not off:
        report.add(Finding("cycle_straddle_declared", "ok", "services/cycle",
                           "every active card bills on the household cycle"))
        return
    last4s = [c.last4 for c in off]
    total = (db.query(func.coalesce(func.sum(Transaction.amount), 0.0))
             .filter(Transaction.household_id == hid, Transaction.txn_type == "SPEND",
                     Transaction.archived.is_(False), Transaction.dup_of_id.is_(None),
                     Transaction.card_last4.in_(last4s)).scalar() or 0.0)
    allsp = (db.query(func.coalesce(func.sum(Transaction.amount), 0.0))
             .filter(Transaction.household_id == hid, Transaction.txn_type == "SPEND",
                     Transaction.archived.is_(False), Transaction.dup_of_id.is_(None)).scalar() or 0.0)
    pct = (total / allsp * 100) if allsp else 0.0
    names = ", ".join(f"{c.bank} {c.last4} (bills {c.statement_day + 1}th)" for c in off)
    report.add(Finding("cycle_straddle_declared", "warn", "services/cycle",
                       f"{pct:.1f}% of all spend is on cards that do NOT bill on the household "
                       f"cycle (starts {hh}th): {names} — their totals are not their bills"))


def check_future_week_targets_fit_what_remains(db: Session, hid: int, report: Report) -> None:
    """Two checks on the weekly bar, and they are not the same KIND of check.

    Every other check in this module compares TOTALS between two modules. None of them looked at a
    per-period target, which is why the weekly bar could show four future weeks each promising the
    full original slice while the budget behind them was already spent — every module agreed on the
    totals the whole time, and the screen was still wrong.

    `weekly_bar_within_budget` is a REGRESSION GUARD, not an invariant, and the difference matters
    enough to name. `weekly_breakdown` builds the future targets to tile what is left after the
    current week's reservation, so the comparison is satisfied by construction and cannot fail on
    anything the function can currently emit. What it WILL catch is the reservation being removed or
    the tiling being changed — which is exactly the defect that shipped — so it earns its place. It
    just must not be mistaken for proof that the bar is right.

    `weekly_spend_matches_the_plan` is the real invariant. The bar's own row scan and the Plan
    headline's `net_spent_inr` are produced by different code over the same transactions, and the
    only thing that should separate them is period-level refunds. That comparison CAN fail, and it
    fails precisely when the two drift on category scope or on the cycle window.
    """
    from api.budget import compute_budget

    try:
        b = compute_budget(db=db, household_id=hid)
    except Exception as exc:  # noqa: BLE001 — a broken collaborator must not hide the other checks
        report.add(Finding("weekly_bar_within_budget", "warn", "api/budget",
                           f"could not run: {type(exc).__name__}"))
        return

    weeks = b.get("weeks") or []
    total_target = float(b.get("total_target_inr") or 0.0)
    if not weeks or total_target <= 0:
        report.add(Finding("weekly_bar_within_budget", "ok", "api/budget",
                           "no weekly targets to check"))
        return

    spent = float(b.get("net_spent_inr") or 0.0)
    remaining = max(0.0, total_target - spent)
    future_sum = round(sum(float(w.get("target") or 0.0) for w in weeks if w.get("is_future")), 2)

    # The week IN PROGRESS has to be in this sum. Leaving it out is what made the first version of
    # this guard worthless: it compared `future_sum` against `remaining` when the future targets are
    # DEFINED to tile `remaining`, so it reported "ok" on every run while the Plan tab told the
    # household it could still spend more than was actually left.
    current = next((w for w in weeks if w.get("is_current")), {})
    headroom = max(0.0, float(current.get("target") or 0.0) - float(current.get("spent") or 0.0))
    still_spendable = round(headroom + future_sum, 2)

    # One allowance sentence, built once. The error and ok branches had drifted into two wordings of
    # the same thing.
    allowance = (f"the weekly bar allows {still_spendable:,.0f} more "
                 f"({headroom:,.0f} left in the current week + {future_sum:,.0f} in the weeks "
                 f"after it)")
    # A paisa of rounding slack. The buckets deliberately tile what is left exactly, so anything past
    # this is a real disagreement rather than float noise.
    if still_spendable > round(remaining, 2) + 0.01:
        report.add(Finding("weekly_bar_within_budget", "error", "api/budget",
                           f"{allowance} but only {remaining:,.0f} of the cycle budget is left "
                           f"({total_target:,.0f} target - {spent:,.0f} spent)"))
    else:
        report.add(Finding("weekly_bar_within_budget", "ok", "api/budget",
                           f"{allowance}, within the {remaining:,.0f} left"))

    # The independent one. `sum(week.spent)` comes from weekly_breakdown's own scan of the ledger,
    # bucketed by date; `net_spent_inr` comes from card_cycle_spend, which totals per CATEGORY and
    # then nets period-level refunds. Two producers, two windowing paths, the same transactions — so
    # the gap between them must be exactly the refunds the same payload publishes. If the weekly bar
    # and the Plan headline ever disagree about which categories count or where the cycle starts,
    # this is what says so.
    weekly_spend = round(sum(float(w.get("spent") or 0.0) for w in weeks), 2)
    refunds = float(b.get("refunds_inr") or 0.0)
    gap = round(weekly_spend - spent, 2)
    if abs(gap - round(refunds, 2)) > 0.02:
        report.add(Finding("weekly_spend_matches_the_plan", "error", "api/budget",
                           f"the weekly bars total {weekly_spend:,.2f} and the Plan headline says "
                           f"{spent:,.2f} net, a gap of {gap:,.2f} — but refunds this period are "
                           f"{refunds:,.2f}. The bar and the headline are counting different rows.",
                           expected=round(refunds, 2), actual=gap,
                           fix="both must read the same categories (plan_categories) over the same "
                               "window; see services/cycle.weekly_breakdown vs card_cycle_spend"))
    else:
        report.add(Finding("weekly_spend_matches_the_plan", "ok", "api/budget",
                           f"weekly bars total {weekly_spend:,.0f}, which is the Plan's "
                           f"{spent:,.0f} net plus {refunds:,.0f} of refunds"))


def check_year_covers_itself_and_matches_the_plan(db: Session, hid: int, report: Report) -> None:
    """The year must be twelve contiguous periods, each equal to what the Plan shows for it.

    Two ways this silently goes wrong, both seen on real data:

      * a period drops out. A whole cycle once vanished from the annual total because the live
        period was addressed differently from the closed ones. A year that skips a month is
        understated by a month and looks entirely normal doing it.
      * a period drifts from the Plan. Then the same household reads two different truths on two
        tabs, and neither screen can tell which of them is wrong.
    """
    from datetime import date, timedelta

    from api.annual import compute_year
    from api.budget import compute_budget

    # None, not date.today().year: on an April financial year those differ for three months of
    # every year, and the check would silently start auditing a year that has not begun.
    y = compute_year(None, db, hid)
    year = y.get("label") or y.get("year")
    months = y.get("months") or []

    if len(months) != 12:
        report.add(Finding("year_covers_itself", "error", "api/annual",
                           f"the year reports {len(months)} periods, not 12",
                           fix="compute_year must emit every month of the calendar year"))
        return

    for prev, nxt in zip(months, months[1:]):
        end = date.fromisoformat(prev["window"]["end"])
        start = date.fromisoformat(nxt["window"]["start"])
        if start - end != timedelta(days=1):
            report.add(Finding(
                "year_covers_itself", "error", "api/annual",
                f"{prev['month']} ends {end} and {nxt['month']} starts {start} — the year has a "
                f"gap or an overlap, so its total is not the sum of its periods",
                fix="address every period with cycle_anchor, including the live one"))
            return

    for m in months:
        if m["state"] == "future":
            continue
        owned = compute_budget(anchor=m["window"]["end"], mode=y.get("mode", "cycle"), db=db,
                               household_id=hid)
        if not _close(owned.get("net_spent_inr"), m.get("net_spent_inr")):
            report.add(Finding(
                "year_matches_plan", "error", "api/budget",
                f"{m['month']}: the year says {m.get('net_spent_inr'):,.0f} and the Plan says "
                f"{owned.get('net_spent_inr'):,.0f} for the same period",
                expected=owned.get("net_spent_inr"), actual=m.get("net_spent_inr"),
                fix="api/budget owns actual spend; the year must read card_cycle_spend, not re-sum"))
            return

    report.add(Finding("year_matches_plan", "ok", "api/annual",
                       f"{year}: 12 contiguous periods, every closed one equal to the Plan"))


def check_windfall_agrees_with_debt(db: Session, hid: int, report: Report) -> None:
    """api/windfall must attack exactly the loans api/debt attacks, and may not free EMI that does
    not exist.

    Windfall re-applies compute_debt's own serviced/beneficiary split to decide where a lump sum can
    go. That is a COPY of a rule, and a copied rule drifts: change which loans are attackable on the
    Debt tab and the windfall screen keeps sending money at a loan the plan has dropped, so one
    household reads two payoff stories on two screens. The avalanche steps are the attack set as
    api/debt itself publishes it, so it is the thing to compare against.

    Also asserts the default amount is read from the modules that own it — cash values from
    api/networth, never a local total.
    """
    from api.debt import compute_debt
    from api.networth import compute_assets
    from api.windfall import compute_windfall

    debt = compute_debt(db, hid)
    win = compute_windfall(db, hid, 0.0)

    ava = next((o for o in debt.get("strategies", {}).get("options", [])
                if o["key"] == "avalanche"), None)
    if ava is None:
        report.add(Finding("windfall_matches_debt", "warn", "api/debt",
                           "no avalanche plan to compare the windfall attack set against"))
        return

    planned = {s["id"] for s in ava.get("steps", [])}
    considered = set(win["prepay"]["considered"])
    if planned != considered:
        missing = planned - considered
        extra = considered - planned
        report.add(Finding(
            "windfall_matches_debt", "error", "api/debt",
            f"the windfall calculator and the payoff plan disagree about which loans can take early "
            f"money — plan-only ids {sorted(missing)}, windfall-only ids {sorted(extra)}",
            expected=float(len(planned)), actual=float(len(considered)),
            fix="windfall must apply the same serviced_by_household + beneficiary split as "
                "api/debt.compute_debt, and no other filter"))
        return

    freed = float(win["prepay"]["emi_freed_per_month_inr"])
    if freed > float(debt.get("total_emi") or 0.0) + TOLERANCE_INR:
        report.add(Finding(
            "windfall_matches_debt", "error", "api/windfall",
            "the windfall frees more monthly EMI than the household actually pays",
            expected=float(debt.get("total_emi") or 0.0), actual=freed))
        return

    cash = win["investable_cash"]
    owner_cash = float(compute_assets(db, hid)["by_type"].get("cash", 0.0))
    # Net worth counts a sale receivable; deployable cash must not, because it has not arrived. So the
    # two agree only once the receivable is added back, and THAT is the relationship to pin.
    recv = cash.get("receivable_inr") or 0.0
    if cash["data_ok"] and not _close(cash["liquid_cash_inr"] + recv, owner_cash):
        report.add(Finding(
            "windfall_matches_debt", "error", "api/networth",
            "the windfall's liquid cash plus its receivable does not reconcile to the asset module",
            expected=owner_cash, actual=cash["liquid_cash_inr"] + recv,
            fix="read by_type['cash'] from compute_assets rather than summing assets locally"))
        return

    # One clock, both sides. The prepay figure is interest avoided INSIDE the window and the invest
    # figure is a projection over it, so if the two windows ever differ the page is answering two
    # questions and inviting the reader to compare the answers. This used to be implicit — the
    # horizon WAS the debt's remaining life — and is asserted now that the household picks it.
    horizon = win["horizon_months"]
    sides = {"invest": win["invest"]["horizon_months"], "prepay": win["prepay"]["horizon_months"]}
    off = {k: v for k, v in sides.items() if v != horizon}
    if off:
        report.add(Finding(
            "windfall_matches_debt", "error", "api/windfall",
            f"the windfall's two sides are projected over different horizons — published "
            f"{horizon} months, but {', '.join(f'{k} uses {v}' for k, v in sorted(off.items()))}",
            expected=float(horizon), actual=float(next(iter(off.values()))),
            fix="pass the one resolved horizon into both _prepay_path and _invest_path, and publish "
                "that same number"))
        return

    report.add(Finding("windfall_matches_debt", "ok", "api/windfall",
                       f"{len(considered)} loan(s) attackable on both the payoff plan and the "
                       f"windfall calculator, both sides projected over the same {horizon} months, "
                       f"and the cash figure comes from api/networth"))


def check_disposal_receipts_have_both_sides(db: Session, hid: int, report: Report) -> None:
    """Every rupee that has left a sale receivable must be on the balance sheet somewhere.

    A settled receivable is two entries: the amount owed falls, and a cash row rises by the same
    amount. They live in different tables and used to be moved by hand in different screens, so
    doing only the first deleted the money from net worth without a single error. Rs 4,00,000 went
    that way, and the earlier Rs 6,50,000 gap was the same shape.

    Three ways this goes wrong, all checked here:

      * a receivable driven below zero — more has been taken off it than the sale ever owed;
      * a receipt recorded with no cash row to receive it, which is the silent deletion itself;
      * receipts plus the remaining receivable exceeding what the sale could ever deliver, which
        means a receipt is filed against the wrong sale or the sale was corrected underneath it.

    The cash row's VALUE is deliberately not asserted. Once money has landed it is ordinary cash and
    the household may spend it, so a falling balance is normal — only its absence is a defect.
    """
    from models.asset import Asset
    from models.disposal import AssetDisposal
    from services.disposals import proceeds_row_name

    disposals = db.query(AssetDisposal).filter(AssetDisposal.household_id == hid).all()
    problems: list[str] = []
    received_total = 0.0
    for d in disposals:
        asset = db.query(Asset).filter(Asset.id == d.asset_id).first()
        label = asset.name if asset else f"disposal {d.id}"
        if (d.receivable_inr or 0.0) < -TOLERANCE_INR:
            problems.append(f"{label}: receivable is negative ({d.receivable_inr:,.2f}) — more has "
                            f"been taken off it than the sale owed")
        received = d.received_against_receivable_inr
        received_total += received
        if received <= 0:
            continue
        name = proceeds_row_name(asset)
        row = db.query(Asset).filter(Asset.household_id == hid, Asset.name == name,
                                     Asset.asset_type == "cash").first()
        if row is None:
            problems.append(f"{label}: {received:,.2f} recorded as received but no {name!r} row "
                            f"holds it — that money has left net worth entirely")
        deliverable = round(d.gross_proceeds_inr - (d.costs_inr or 0.0)
                            - (d.loan_settled_inr or 0.0), 2)
        if received + (d.receivable_inr or 0.0) > deliverable + TOLERANCE_INR:
            problems.append(f"{label}: {received:,.2f} received plus {d.receivable_inr:,.2f} still "
                            f"owed exceeds the {deliverable:,.2f} this sale can deliver")

    if problems:
        report.add(Finding(
            "disposal_receipts_both_sides", "error", "services/disposals",
            "; ".join(problems),
            fix="money arriving against a sale must go through record_receipt, which lowers the "
                "receivable and credits the sale's cash row in one transaction — never edit "
                "receivable_inr on its own"))
    else:
        report.add(Finding("disposal_receipts_both_sides", "ok", "services/disposals",
                           f"{len(disposals)} sale(s), {received_total:,.0f} received and all of it "
                           f"accounted for on the balance sheet"))


def check_composition_covers_total_assets(db: Session, hid: int, report: Report) -> None:
    """The Wealth tab's five buckets must add up to the total assets the headline quotes.

    Composition groups holdings by NATURE; compute_networth groups them by the table they came from.
    A new asset type, recurring kind or fund that no bucket claims leaves net worth perfectly right
    and the breakdown quietly short — the tab and the headline then disagree with nothing failing,
    which is the same shape as the Rs 6,50,000 receivable that went missing from one of the two
    asset queries."""
    from api.networth import compute_composition, compute_networth

    total = compute_networth(db, hid)["total_assets"]
    bucketed = round(sum(b["value_inr"] for b in compute_composition(db, hid)["buckets"]), 2)
    if not _close(bucketed, total):
        report.add(Finding(
            "composition_covers_total_assets", "error", "api/networth",
            "the composition buckets do not add up to total assets — a holding is in no bucket",
            expected=total, actual=bucketed,
            fix="every asset type, recurring kind and sinking fund must land in one of the five "
                "buckets in api/networth._BUCKETS"))
    else:
        report.add(Finding("composition_covers_total_assets", "ok", "api/networth",
                           f"five buckets account for all {total:,.0f} of assets"))


CHECKS = (
    check_leftover_closes_the_waterfall,
    check_cockpit_does_not_double_subtract,
    check_networth_endpoints_agree,
    check_composition_covers_total_assets,
    check_emi_single_source,
    check_transactions_reconcile_with_ledgers,
    check_recurring_classification,
    check_gold_loans_not_amortised,
    check_analytics_agrees_with_plan,
    check_year_covers_itself_and_matches_the_plan,
    check_future_week_targets_fit_what_remains,
    check_no_misparsed_rows,
    check_sync_is_alive,
    check_no_card_purchase_typed_as_emi,
    check_cycle_straddle_is_declared,
    check_loans_are_self_consistent,
    check_windfall_agrees_with_debt,
    check_disposal_receipts_have_both_sides,
)


def run_all(db: Session, household_id: int) -> dict:
    """Run every invariant. A check that itself blows up is reported, never propagated."""
    report = Report()
    for check in CHECKS:
        try:
            check(db, household_id, report)
        except Exception as exc:  # noqa: BLE001 — one broken check must not hide the others
            logger.exception("consistency check %s failed", check.__name__)
            report.add(Finding(check.__name__, "warn", "services/consistency",
                               f"check could not run: {exc}"))
    return report.as_dict()
