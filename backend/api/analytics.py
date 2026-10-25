import re
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from api.deps import get_current_household
from database import get_db
from models.tenant import Household
from models.transaction import Transaction

router = APIRouter()

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _shift_month(month: str, delta: int) -> str:
    """'YYYY-MM' shifted by `delta` calendar months (delta may be negative)."""
    y, m = map(int, month.split("-"))
    idx = y * 12 + (m - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _cycle_start_day(db: Session | None = None, household_id: int | None = None) -> int:
    # Must be the HOUSEHOLD's billing day, not the global default — else a cycle column is
    # labelled with a window the numbers inside it never came from.
    from services.cycle import household_start_day
    return household_start_day(db, household_id)


def _one_day():
    from datetime import timedelta
    return timedelta(days=1)


def _month_label(month: str) -> str:
    y, m = map(int, month.split("-"))
    return date(y, m, 1).strftime("%B %Y")


def _period(mode: str, label: str, frm: str | None = None, to: str | None = None,
            start: str | None = None, end: str | None = None) -> dict:
    """Every analytics response carries this so a card can label itself from the data instead of
    assuming a period the endpoint never applied."""
    return {"mode": mode, "label": label, "from": frm, "to": to, "start": start, "end": end}


def _current_month() -> str:
    t = date.today()
    return f"{t.year:04d}-{t.month:02d}"


@router.get("/analytics/trend")
def analytics_trend(db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)):
    # account spend IS counted (txn_type=SPEND already excludes salary/EMI/transfers) — consistent slice.
    debit_sum = func.sum(
        case(((Transaction.direction == "debit") & (Transaction.txn_type == "SPEND"),
              Transaction.amount), else_=0.0)
    )
    # `net` used to be (every credit − spend), so card-bill payments and inter-account transfers
    # inflated it. Only INCOME is money actually arriving; gross inflow stays separately labelled.
    income_sum = func.sum(
        case((Transaction.txn_type == "INCOME", Transaction.amount), else_=0.0)
    )
    credit_sum = func.sum(
        case((Transaction.direction == "credit", Transaction.amount), else_=0.0)
    )

    rows = (
        db.query(
            Transaction.stmt_month.label("month"),
            debit_sum.label("total_spend"),
            income_sum.label("total_income"),
            credit_sum.label("total_credit"),
        )
        .filter(Transaction.household_id == household.id)  # tenant-scoped
        .filter(Transaction.dup_of_id.is_(None))  # never count a merged duplicate
        .filter(Transaction.archived.is_(False))  # never count an archived (removed) txn
        .group_by(Transaction.stmt_month)
        .order_by(Transaction.stmt_month)
        .all()
    )

    months = []
    for row in rows:
        total_spend = round(row.total_spend or 0.0, 2)
        total_income = round(row.total_income or 0.0, 2)
        total_credit = round(row.total_credit or 0.0, 2)
        months.append(
            {
                "month": row.month,
                "total_spend": total_spend,
                "total_income": total_income,
                "total_credit": total_credit,   # gross inflow incl. transfers + card-bill credits
                "net": round(total_income - total_spend, 2),
            }
        )

    keys = [m["month"] for m in months if m["month"]]
    lo, hi = (keys[0], keys[-1]) if keys else (None, None)
    label = (f"{_month_label(lo)} – {_month_label(hi)}" if lo and hi and lo != hi
             else _month_label(lo) if lo else "No data")
    return {"period": _period("calendar_month", label, lo, hi), "months": months}


@router.get("/analytics/category-matrix")
def analytics_category_matrix(
    from_month: str | None = None,
    to_month: str | None = None,
    db: Session = Depends(get_db),
    household: Household = Depends(get_current_household),
):
    """Month-on-month VARIABLE spend per category — the Analytics matrix in the 'By month' lens. Uses
    the shared Money Slice Contract (services/money_slice): variable_spend, calendar month, ALL sources
    (card + account), so it reconciles with Plan. Each cell carries `idsByMonth` so a drill matches the
    number. YoY present only where a prior-year month has data."""
    from services import money_slice
    m = money_slice.matrix(db, household.id, kind="variable_spend", cycle_mode="calendar_month")
    months = [x for x in m["months"] if (not from_month or x >= from_month) and (not to_month or x <= to_month)]
    mset = set(months)
    grand = {k: v for k, v in m["grand_total_by_month"].items() if k in mset}
    categories = []
    for c in m["categories"]:
        byM = {k: v for k, v in c["byMonth"].items() if k in mset}
        if not byM:
            continue
        idsM = {k: v for k, v in c["idsByMonth"].items() if k in mset}
        tot = round(sum(byM.values()), 2)
        yoy = {mo: round((v - byM[_shift_month(mo, -12)]) / byM[_shift_month(mo, -12)] * 100, 1)
               for mo, v in byM.items() if byM.get(_shift_month(mo, -12), 0) > 0}
        categories.append({"category": c["category"], "byMonth": byM, "idsByMonth": idsM,
                           "total": tot, "avg": round(tot / max(1, len(byM)), 2), "yoyByMonth": yoy})
    categories.sort(key=lambda x: -x["total"])
    lo, hi = (months[0], months[-1]) if months else (None, None)
    label = (f"{_month_label(lo)} – {_month_label(hi)}" if lo and hi and lo != hi
             else _month_label(lo) if lo else "No data")
    return {"period": _period("calendar_month", label, lo, hi),
            "months": months, "categories": categories, "grand_total_by_month": grand,
            "refunds_by_month": {k: v for k, v in m["refunds_by_month"].items() if k in mset}}


@router.get("/analytics/cycle-matrix")
def analytics_cycle_matrix(
    count: int = 12,
    db: Session = Depends(get_db),
    household: Household = Depends(get_current_household),
):
    """Cycle-on-cycle VARIABLE spend per category — the Analytics matrix in the 'This cycle' lens. Uses
    the shared Money Slice (instrument_cycle): every txn is bucketed by its OWN card's cycle offset from
    now and labelled by the household cycle's close-month, so the current column equals Plan's per-card
    'this cycle' to the rupee. Columns are labelled by their representative window (e.g. 12 Jun – 11 Jul)."""
    from datetime import date

    from services import money_slice
    m = money_slice.matrix(db, household.id, kind="variable_spend", cycle_mode="instrument_cycle")
    keep = m["months"][-count:]
    keepset = set(keep)
    sd = _cycle_start_day(db, household.id)

    def _window(close_month: str) -> tuple[str, str]:
        y, mo = map(int, close_month.split("-"))
        end_excl = date(y, mo, min(sd, 28))                 # the cycle closes the day before its start-day
        py, pm = (y - 1, 12) if mo == 1 else (y, mo - 1)
        return date(py, pm, min(sd, 28)).isoformat(), (end_excl - _one_day()).isoformat()

    cycles = [{"key": k, "start": _window(k)[0], "end": _window(k)[1]} for k in keep]
    categories = []
    for c in m["categories"]:
        byc = {k: v for k, v in c["byMonth"].items() if k in keepset}
        if not byc:
            continue
        ids = {k: v for k, v in c["idsByMonth"].items() if k in keepset}
        tot = round(sum(byc.values()), 2)
        categories.append({"category": c["category"], "byCycle": byc, "idsByCycle": ids,
                           "total": tot, "avg": round(tot / max(1, len(byc)), 2)})
    categories.sort(key=lambda x: -x["total"])
    label = (f"{cycles[0]['start']} – {cycles[-1]['end']}" if cycles else "No data")
    return {
        "period": _period("instrument_cycle", label,
                          keep[0] if keep else None, keep[-1] if keep else None,
                          cycles[0]["start"] if cycles else None,
                          cycles[-1]["end"] if cycles else None),
        "cycles": cycles,
        "categories": categories,
        "grand_total_by_cycle": {k: m["grand_total_by_month"][k] for k in keep},
        "refunds_by_cycle": {k: m["refunds_by_month"].get(k, 0.0) for k in keep},
    }


@router.get("/analytics/recommendations")
def analytics_recommendations(
    narrate: bool = True,
    cycle_mode: str = "instrument_cycle",
    db: Session = Depends(get_db),
    household: Household = Depends(get_current_household),
):
    """Deterministic budget-target recommendations: median of the last 6 clean PERIODS per category
    (spike periods and the partial current one excluded) + a min–max band + a headline signal. The
    period follows `cycle_mode` (instrument_cycle → billing cycles; calendar_month → months) so the
    suggestion matches the lens you're viewing. `narrate=true` lets the LLM rephrase each rationale
    (numbers stay final); it degrades to the deterministic text if the model is unavailable."""
    from services import recommendations as rec
    out = rec.recommend(db, household.id, cycle_mode=cycle_mode)
    # The current period is deliberately excluded (it's partial), so the window ENDS one period back.
    last = _shift_month(out["generated_for"], -1)
    unit, window = out.get("unit", "period"), out.get("window", rec.WINDOW)
    out["period"] = _period(cycle_mode, f"up to {window} clean {unit}s ending {_month_label(last)}",
                            _shift_month(last, -(window - 1)), last)
    return rec.narrate(out) if narrate else out
