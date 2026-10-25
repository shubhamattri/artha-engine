"""The "This month" cockpit — one aggregation that answers "are we OK + what to do".

Composes the existing engines (cashflow / debt / net worth / sinking) into a single payload so the
home screen makes ONE request (no N+1). Nudges are derived here (server-side, testable). This is the
forward-looking home: safe-to-spend, what's coming, what to look at, and whether we're getting ahead.
"""

from __future__ import annotations

from api.deps import get_current_household
from database import get_db
from fastapi import APIRouter, Depends
from models.tenant import Household
from sqlalchemy.orm import Session

router = APIRouter()


def _nudges(cash: dict, loans: list[dict], sinking_monthly: float,
            symbol: str | None = None) -> list[dict]:
    from services.money_format import DEFAULT_SYMBOL
    c = symbol or DEFAULT_SYMBOL
    out: list[dict] = []
    top = loans[:2]
    if top:
        names = " and ".join(f'{l["rate_pct"]}% {l["name"].split(" Loan")[0].split(" CC")[0]}' for l in top)
        total = sum(l["balance_inr"] for l in top)
        out.append({
            "tone": "negative", "title": "Clear your costliest loans first",
            "body": f"{names} are your highest rates — clearing {c}{total:,.0f} kills the most interest per unit.",
            "cta": "See payoff order",
        })
    income = cash["income"] or 1
    rate = round((cash["saving"] + cash["sinking"] + cash["insurance"]) / income * 100)
    out.append({
        "tone": "warn", "title": f"You're saving {rate}% of income",
        "body": f"Every extra 5% is about {c}{income * 0.05 * 12:,.0f}/yr you could throw at debt or goals.",
        "cta": "Open plan",
    })
    if not sinking_monthly:
        out.append({
            "tone": "accent", "title": "No sinking funds yet",
            "body": "Set aside monthly for lumpy costs (travel, repairs, insurance) so they don't blow a month.",
            "cta": "Set up",
        })
    return out


def _coming_up(db: Session, household_id: int) -> list[dict]:
    """The real recurring outflows still due — grouped (EMIs, SIPs, rent/household, insurance), each
    expandable to its individual commitments. Drawn from the actual defined commitments (not hardcoded);
    each item's due DAY is inferred, best-effort, from its most recent matching debit (None if unknown)."""
    from datetime import date, timedelta

    from models.transaction import Transaction
    from services.commitments import list_commitments

    commits = list_commitments(db, household_id)
    # last ~3 months of real debits, most-recent first → infer each commitment's due DAY from the most
    # recent debit near its amount (±3%, so a ₹50,400 EMI still matches its ₹50,000 auto-debit).
    since = (date.today() - timedelta(days=95)).isoformat()
    rows = (db.query(Transaction)
            .filter(Transaction.direction == "debit", Transaction.dup_of_id.is_(None),
                    Transaction.archived.is_(False), Transaction.household_id == household_id,
                    Transaction.date >= since).all())
    recent: list[tuple[float, int]] = []
    for t in sorted(rows, key=lambda r: r.date, reverse=True):
        try:
            recent.append((round(t.amount, 2), int(t.date[8:10])))
        except (ValueError, IndexError):
            pass

    def _due_day(amount: float) -> int | None:
        tol = max(200.0, amount * 0.03)
        for a, d in recent:                    # most recent first
            if abs(a - amount) <= tol:
                return d
        return None

    groups_spec = [("Loan EMIs", {"EMI"}), ("SIPs & savings", {"SIP", "Saving"}),
                   ("Rent & household", {"Fixed"}), ("Insurance", {"Insurance"})]
    out: list[dict] = []
    for label, kinds in groups_spec:
        items = [{"name": c["name"], "amount": c["amount"], "day": _due_day(c["amount"])}
                 for c in commits if c["kind"] in kinds]
        if items:
            out.append({"label": label, "amount": round(sum(i["amount"] for i in items), 2),
                        "count": len(items), "items": sorted(items, key=lambda x: -x["amount"])})
    return out


@router.post("/advisor")
def ask_advisor(body: dict, db: Session = Depends(get_db),
                household: Household = Depends(get_current_household)):
    """Ask Artha — chat-based financial advisor with full household context (income, loans, net worth,
    assets, spend). Body: {messages:[{role,content}], period?:'cycle'|'month', chat_id?:int}.
    Returns {reply, chat_id?, message_id?}. When settings.advisor_grounded is True the request routes
    through the tool-calling grounded agent (persists the turn + returns ids for feedback); when False
    (default) it uses the legacy prompt-based advisor, byte-for-byte unchanged."""
    from config import settings
    b = body or {}
    period = "month" if b.get("period") == "month" else "cycle"   # active lens; default per-card cycle
    if getattr(settings, "advisor_grounded", False):
        from services.advisor_agent import reply as grounded_reply
        out = grounded_reply(db, b.get("messages", []), household.id, period, b.get("chat_id"))
        text = out.get("text") or "I couldn't generate a response just now — try again."
        return {"reply": text, "chat_id": out.get("chat_id"),
                "message_id": out.get("assistant_message_id")}
    from services.advisor import reply
    return {"reply": reply(db, b.get("messages", []), household.id, period)}


@router.get("/health")
def financial_health(db: Session = Depends(get_db),
                     household: Household = Depends(get_current_household)):
    """Where every rupee of income goes, as live percentages. 100% formula-driven from the cashflow
    engine (income lines, loan EMIs, savings/investments, fixed, sinking, insurance) — so changing a
    loan, a salary, or a savings line instantly moves these rates. Nothing hard-coded."""
    from api.cashflow import compute_cashflow
    c = compute_cashflow(db, household.id)
    income = c["income"] or 0
    div = income or 1

    def pct(x: float) -> float:
        return round(x / div * 100, 1)

    savings = round(c["saving"] + c["sinking"], 2)         # everything you set aside (incl. SIPs)
    # one DISTINCT colour per row
    buckets = [
        {"label": "Savings & investments", "amount": savings, "pct": pct(savings), "color": "#0F7A52"},
        {"label": "Loan EMIs", "amount": c["emi"], "pct": pct(c["emi"]), "color": "#C0533B"},
        {"label": "Fixed expenses", "amount": c["fixed"], "pct": pct(c["fixed"]), "color": "#D99A2B"},
        {"label": "Insurance", "amount": c["insurance"], "pct": pct(c["insurance"]), "color": "#7C5CBF"},
        {"label": "Left for living", "amount": c["leftover"], "pct": pct(c["leftover"]), "color": "#4E8D7C"},
    ]
    return {
        "income": income,
        "buckets": buckets,
        "savings_rate": pct(savings),
        "emi_rate": pct(c["emi"]),
        "fixed_rate": pct(c["fixed"]),
        "insurance_rate": pct(c["insurance"]),
        "free_rate": pct(c["leftover"]),
    }


@router.get("/insights")
def get_insights(mode: str = "cycle", db: Session = Depends(get_db),
                 household: Household = Depends(get_current_household)):
    """Live AI insights for the chosen lens (cycle|month) — deterministic detectors narrated by the
    LLM. Powers Home's 'What to look at' and the Insights view."""
    from services.insights import generate
    return generate(db, mode=mode, household_id=household.id)


def _needs_you(db: Session, household_id: int, cstart, cend) -> list[dict]:
    """The things only a person can settle, in one ranked list.

    Every item is COUNTED, never inferred. An item with nothing behind it is omitted entirely rather
    than shown at zero: "0 transactions need a category" is a row that costs attention to read and
    then tells you to do nothing.
    """
    from datetime import date

    from models.insurance import InsurancePolicy
    from models.transaction import Transaction

    out: list[dict] = []

    # 1. Unfiled spend. A transaction with no category is invisible to every target on the Plan, so
    #    the cycle's own figures are incomplete until these are filed — which is why it leads.
    rows = (db.query(Transaction)
            .filter(Transaction.household_id == household_id,
                    Transaction.dup_of_id.is_(None), Transaction.archived.is_(False),
                    Transaction.direction == "debit",
                    Transaction.category.is_(None),
                    Transaction.date >= cstart.isoformat(),
                    Transaction.date < cend.isoformat())
            .all())
    if rows:
        out.append({"kind": "uncategorised", "count": len(rows),
                    "amount_inr": round(sum(r.amount or 0.0 for r in rows), 2),
                    "to": "/spending"})

    # 2. Possible duplicates. Same engine as the Spending tab's review, so the count here and the list
    #    there can never disagree.
    try:
        from services import dedup
        cands = dedup.review_candidates(db, household_id) or []
        if cands:
            out.append({"kind": "duplicates", "count": len(cands), "to": "/spending"})
    except Exception as exc:            # a detector that breaks must not take the daily screen down
        import logging
        logging.getLogger(__name__).warning("duplicate review unavailable: %r", exc)

    # 3. A policy about to renew. An annual-premium policy renews on its start-date anniversary, which
    #    is a fact about the policy rather than a guess; monthly-premium policies have no renewal date
    #    to miss, so they are not considered.
    soon: list[dict] = []
    today = date.today()
    for p in (db.query(InsurancePolicy)
              .filter(InsurancePolicy.household_id == household_id,
                      InsurancePolicy.active.is_(True)).all()):
        if (p.premium_frequency or "").lower() != "annual" or not p.start_date:
            continue
        try:
            nxt = p.start_date.replace(year=today.year)
        except ValueError:              # 29 Feb on a non-leap year
            continue
        if nxt < today:
            nxt = nxt.replace(year=today.year + 1)
        days = (nxt - today).days
        if 0 <= days <= 30:
            soon.append({"provider": p.provider, "kind_of_cover": p.kind,
                         "premium_inr": round(p.premium or 0.0, 2),
                         "sum_assured_inr": round(p.sum_assured or 0.0, 2),
                         "renews_on": nxt.isoformat(), "in_days": days})
    if soon:
        soon.sort(key=lambda s: s["in_days"])
        out.append({"kind": "renewal", "count": len(soon), "policies": soon[:3],
                    "in_days": soon[0]["in_days"], "to": "/balance"})
    return out


@router.get("/cockpit")
def get_cockpit(mode: str = "cycle", db: Session = Depends(get_db),
                household: Household = Depends(get_current_household)):
    # Local imports avoid import-time cycles (same pattern as networth/cashflow).
    from datetime import date

    from api.cashflow import compute_cashflow
    from api.debt import compute_debt
    from api.networth import compute_networth
    from api.sinking import compute_sinking
    from services.cycle import cycle_bounds, cycle_label
    from services.cycle import household_start_day as _hh_start
    from services.money_format import household_symbol

    cash = compute_cashflow(db, household.id)
    debt = compute_debt(db, household.id)
    nw = compute_networth(db, household.id)
    sink = compute_sinking(db, household.id)

    # cycle-to-date spend — billing-cycle-aware PER CARD (ICICI bills the 12th, HDFC/Axis the 22nd),
    # so a card's spend only counts once its own current cycle has started. Cash/UPI use the 12th.
    from services.cycle import card_cycle_spend, month_bounds
    if mode == "month":
        from datetime import timedelta
        cstart, last = month_bounds(date.today()); cend = last + timedelta(days=1)
    else:
        cstart, cend = cycle_bounds(date.today(), _hh_start(db, household.id))
    # same engine + mode as the Plan, so Home and Plan reconcile under either lens.
    spent_cycle = card_cycle_spend(db, date.today(), mode=mode, household_id=household.id)

    # canonical still-due picture (one source with Commitments) — reconciler-driven, not amount-guessing.
    from services.commitments import reconcile as _reconcile
    from services.month_state import coming_up_from_reconcile as _cuf
    _rec = _reconcile(db, household.id, date.today().strftime("%Y-%m"))
    _month_coming_up = _cuf(_rec)
    _remaining_dues = round(_rec.get("missing_total", 0.0), 2)

    # ── PACE — the one graphic the hero earns ──────────────────────────────────────────────
    # The hero used to take its colour from (spent / days_elapsed) × days_total, computed in the
    # frontend. api/budget.py rejects that formula in its own source — "NOT spent/days*total, that
    # scales one-off lumps as if they recurred daily" — so the screen was colouring itself with the
    # one projection the owning module refuses to publish. Six days into a cycle a single furniture
    # purchase multiplied by five and reported itself as a pace.
    #
    # Read the owner instead. compute_budget's per-category projection counts lumps ONCE and
    # extrapolates only the drip, and its cycle window is the same one the Plan tab shows, so the two
    # tabs cannot disagree about whether the household is on track.
    from api.budget import compute_budget
    _bud = compute_budget(mode=mode, db=db, household_id=household.id)
    _items = _bud.get("items") or []
    _bcycle = _bud.get("cycle") or {}
    days_total = int(_bcycle.get("days_total") or (cend - cstart).days or 1)
    days_elapsed = max(1, int(_bcycle.get("days_elapsed") or 1))
    days_left = max(0, int(_bcycle.get("days_left") or 0))

    allowed = round(cash["leftover"], 2)          # what is AVAILABLE to spend, per the cashflow owner
    safe = round(allowed - spent_cycle, 2)
    projected = round(sum(i.get("projected_inr") or 0.0 for i in _items), 2)
    # Drip only: the daily habit with the lumps taken out. Using total spend here would let one big
    # purchase claim the household "runs out" tomorrow, which is the same lie in a different place.
    drip = round(sum(i.get("recurring_inr") or 0.0 for i in _items), 2)
    daily = drip / days_elapsed if drip > 0 else 0.0

    days_early = None
    if safe > 0 and daily > 0 and allowed > 0:
        to_exhaust = safe / daily
        if to_exhaust < days_left:
            days_early = max(1, round(days_left - to_exhaust))

    if safe < 0:
        pace_state = "over"
    elif allowed > 0 and (projected > allowed or days_early):
        pace_state = "tight"
    else:
        pace_state = "on_track"

    loans = sorted(
        (l for l in debt["loans"] if not l["closed"] and l["balance_inr"] > 0),
        key=lambda l: -l["rate_pct"],
    )
    income = cash["income"] or 1
    saved = cash["saving"] + cash["sinking"]
    _months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    label = (f"{_months[date.today().month]} {date.today().year}" if mode == "month"
             else cycle_label(date.today(), _hh_start(db, household.id)))
    return {
        # declares the Money Slice this figure reads: variable_spend, per-card cycle, all sources, NET
        # of refunds (same slice as Plan's Net spent).
        "slice": {"kind": "variable_spend",
                  "cycle_mode": "calendar_month" if mode == "month" else "instrument_cycle",
                  "sources": "all", "net_of_refunds": True},
        "cycle": {"label": label, "mode": mode,
                  "days_left": days_left, "days_total": days_total,
                  "days_elapsed": days_elapsed,
                  "spent": round(spent_cycle, 2)},
        "safe_to_spend": safe,
        # One hero, three states, and the STATE comes from here rather than from a formula in the
        # frontend. `days_early` is null unless the drip rate genuinely exhausts what is left before
        # the cycle ends, so the screen can never invent "runs out early" out of one big purchase.
        "pace": {
            "allowed_inr": allowed,
            "spent_inr": round(spent_cycle, 2),
            "projected_inr": projected,
            # Two percentages, deliberately separate: the bar is how much of the allowance is gone,
            # the marker is how much of the cycle is gone. The whole point of the graphic is that
            # those two differ.
            "spent_pct": round(spent_cycle / allowed * 100, 1) if allowed > 0 else None,
            "elapsed_pct": round(days_elapsed / days_total * 100, 1),
            "days_early": days_early,
            "state": pace_state,
        },
        "glance": {
            "income": cash["income"],
            "committed": round(cash["emi"] + cash["fixed"] + cash["insurance"], 2),
            "saved": round(saved, 2),
            "leftover": cash["leftover"],
        },
        # still-due this month, from the canonical month-state (reconciler-driven — only UNPAID
        # commitments, not amount-guessing #5). remaining_dues = what still has to leave this month.
        "coming_up": _month_coming_up,
        "remaining_dues": _remaining_dues,
        # the ONE money move — same engine as the Debt tab, so the whole app speaks with one voice.
        "move": debt.get("strategies", {}).get("move"),
        # Five things at equal weight is no ranking at all, which is what the old stack of insight
        # cards was. One named move above, and everything that needs a HUMAN decision collected here.
        "needs_you": _needs_you(db, household.id, cstart, cend),
        "nudges": _nudges(cash, loans, sink["monthly_set_aside"],
                          household_symbol(db, household.id)),
        "ahead": {
            "savings_rate": round((saved + cash["insurance"]) / income * 100),
            "debt_total": debt["total_balance"],
            "loan_count": len(loans),
            "sinking_total": sink["total_balance"],
            "net_worth": nw["net_worth"],
        },
    }
