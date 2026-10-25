"""Net worth — assets CRUD + valuation.

Stocks are valued qty × price × FX→INR using a stale-tolerant feed (cached on the
row). Non-market assets (FD/EPF/cash) use a manual INR value. All editable.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_household
from database import get_db
from models.asset import Asset
# Registered at module level (networth is imported by main at startup) so the disposals table is
# mapped before any query touches it — same reason LoanPayment is imported eagerly in api/debt.py.
from models.disposal import AssetDisposal, DisposalReceipt  # noqa: F401  (registers the mappers)
from models.tenant import Household
from services import prices

logger = logging.getLogger(__name__)

router = APIRouter()

STALE_AFTER = timedelta(hours=6)


class AssetIn(BaseModel):
    name: str
    asset_type: str = "stock"          # stock | cash | property | gold | vehicle | other
    holder: str | None = None
    ticker: str | None = None
    quantity: float | None = None
    currency: str = "INR"
    manual_value_inr: float | None = None
    meta: dict | None = None            # gold → {purity}; vehicle → {purchase_price, purchase_date}
    notes: str | None = None


class DisposalIn(BaseModel):
    disposed_on: date
    gross_proceeds_inr: float
    acquired_on: date | None = None
    quantity: float | None = None       # null = the whole asset went
    costs_inr: float = 0.0              # brokerage/stamp duty — REDUCES the taxable gain
    loan_settled_inr: float = 0.0       # loan closed from the proceeds — reduces the gain by NOTHING
    receivable_inr: float = 0.0
    cost_basis_inr: float | None = None  # null = gain not computable, never zero
    notes: str | None = None


class DisposalPatch(BaseModel):
    """Every field optional: the two disposals already in the DB carry invented placeholder dates
    that have to be correctable without re-entering the sale."""

    disposed_on: date | None = None
    gross_proceeds_inr: float | None = None
    acquired_on: date | None = None
    quantity: float | None = None
    costs_inr: float | None = None
    loan_settled_inr: float | None = None
    receivable_inr: float | None = None
    cost_basis_inr: float | None = None
    notes: str | None = None


class ReceiptIn(BaseModel):
    """Money that has actually arrived against a sale's receivable. Deliberately NOT a new value for
    receivable_inr: an absolute figure cannot say how much moved, so it cannot move the cash side."""

    received_on: date
    amount_inr: float
    notes: str | None = None


def _value(asset: Asset, db: Session, force: bool = False) -> dict:
    """Compute current INR value; refresh market prices if stale/forced."""
    from datetime import date as _date

    priced = "manual"
    meta = asset.meta or {}

    # GOLD — grams × live per-gram resale value for the purity (cached 6h on the row).
    if asset.asset_type == "gold" and asset.quantity:
        purity = meta.get("purity", "24k")
        now = datetime.now(timezone.utc)
        stale = (force or asset.last_price is None or asset.last_price_at is None
                 or asset.last_price_at.replace(tzinfo=timezone.utc) < now - STALE_AFTER)
        if stale:
            per_g = prices.fetch_gold_per_gram(purity)
            if per_g:
                asset.last_price = per_g
                asset.last_price_at = now.replace(tzinfo=None)
                db.add(asset)
        if asset.last_price:
            return {"value_inr": round(asset.last_price * asset.quantity, 2),
                    "priced": "live" if stale else "cached",
                    "unit_price": round(asset.last_price, 2),
                    "as_of": asset.last_price_at.isoformat() if asset.last_price_at else None}

    # VEHICLE — purchase price depreciated by the standard IDV schedule (computed, no feed).
    if asset.asset_type == "vehicle" and meta.get("purchase_price") and meta.get("purchase_date"):
        from services.depreciation import vehicle_value
        try:
            pd = _date.fromisoformat(meta["purchase_date"])
            r = vehicle_value(float(meta["purchase_price"]), pd, _date.today())
            return {"value_inr": r["value"], "priced": "computed",
                    "unit_price": None, "as_of": f'−{r["depreciation_pct"]}% @ {r["age_years"]}y'}
        except Exception:
            pass

    # PROPERTY — purchase price appreciated at a configurable annual rate (computed, no feed).
    if asset.asset_type == "property" and meta.get("purchase_price") and meta.get("purchase_date"):
        from services.depreciation import appreciate
        try:
            pd = _date.fromisoformat(meta["purchase_date"])
            r = appreciate(float(meta["purchase_price"]), pd, float(meta.get("appreciation_pct", 7.0)), _date.today())
            return {"value_inr": r["value"], "priced": "computed",
                    "unit_price": None, "as_of": f'+{r["appreciation_pct"]}%/yr · {r["age_years"]}y'}
        except Exception:
            pass

    if asset.asset_type == "stock" and asset.ticker and asset.quantity:
        now = datetime.now(timezone.utc)
        stale = (
            force or asset.last_price is None or asset.last_price_at is None
            or asset.last_price_at.replace(tzinfo=timezone.utc) < now - STALE_AFTER
        )
        if stale:
            fetched = prices.fetch_price(asset.ticker)
            if fetched:
                asset.last_price = fetched[0]
                asset.last_price_at = now.replace(tzinfo=None)
                if asset.currency == "INR" and fetched[1] != "INR":
                    asset.currency = fetched[1]
                db.add(asset)
        if asset.last_price:
            fx = prices.fx_to_inr(asset.currency)
            value = asset.last_price * asset.quantity * fx
            priced = "live" if stale else "cached"
            return {"value_inr": round(value, 2), "priced": priced,
                    "unit_price": round(asset.last_price, 2),
                    "as_of": asset.last_price_at.isoformat() if asset.last_price_at else None}
    return {"value_inr": round(asset.manual_value_inr or 0.0, 2), "priced": priced,
            "unit_price": None, "as_of": None}


def _view(asset: Asset, db: Session, force: bool = False) -> dict:
    v = _value(asset, db, force)
    return {
        "id": asset.id, "name": asset.name, "asset_type": asset.asset_type,
        "holder": asset.holder, "ticker": asset.ticker, "quantity": asset.quantity,
        "currency": asset.currency, "manual_value_inr": asset.manual_value_inr,
        "meta": asset.meta or {}, "notes": asset.notes, **v,
    }


def compute_assets(db: Session, household_id: int | None = None, refresh: bool = False) -> dict:
    """Asset list + totals + by-type breakdown. Plain helper so internal callers (afford, advisor)
    can use it directly; the route wraps it with the current household. household_id=None = all."""
    q = db.query(Asset).filter(Asset.active.is_(True))
    if household_id is not None:
        q = q.filter(Asset.household_id == household_id)
    rows = q.all()
    assets = [_view(a, db, force=refresh) for a in rows]
    # Proceeds from a completed sale that have not landed yet. The sold asset is already excluded
    # (disposal deactivates it), so without this the money disappears between sale and settlement.
    # Only the UNRECEIVED part: cash already banked is visible to the statement pipeline, and adding
    # it here too is exactly the double-count this replaces.
    try:
        from services.disposals import outstanding_receivables
        assets.extend(outstanding_receivables(db, household_id))
    except Exception:  # noqa: BLE001 — never let this take down the net-worth page
        logger.exception("could not add disposal receivables")
    db.commit()  # persist any refreshed prices
    total = round(sum(a["value_inr"] for a in assets), 2)
    by_type: dict[str, float] = {}
    for a in assets:
        by_type[a["asset_type"]] = round(by_type.get(a["asset_type"], 0.0) + a["value_inr"], 2)
    return {"assets": assets, "total_inr": total, "by_type": by_type}


@router.get("/networth")
def get_networth(refresh: bool = False, db: Session = Depends(get_db),
                 household: Household = Depends(get_current_household)):
    return compute_assets(db, household.id, refresh=refresh)


# Net-worth category for a manual/market asset type.
_ASSET_CATEGORY = {"stock": "Market", "cash": "Cash & Bank", "property": "Property",
                   "gold": "Gold", "vehicle": "Vehicles"}


def _gold_collateral(assets: list[dict], loans: list[dict]) -> dict:
    """Gold on the balance sheet done right: a gold LOAN is a LIABILITY, not gold vanishing. When you
    borrow ₹10L against ₹62L of gold, the gold still keeps its full ₹62L value — you just owe ₹10L. So:
        real value  = total gold assets, live (grows as you buy more / as the price rises)
        pledged     = balances of active 'Gold Loan' loans (straight from the debt section) + any pledge
                      held OUTSIDE Artha (a parent's existing loan, config) — this is money OWED, not gold lost
        net value   = real − pledged   → your actual equity in the gold
        borrow room = real × LTV − pledged   → how much MORE you could still raise against it
    Add or close a gold loan in the debt section and every figure below recomputes on its own — a loan
    marked PAID leaves the active set, so pledged drops and net value climbs back up automatically."""
    from config import settings
    ltv = settings.gold_ltv or 0.75
    gold_assets = [a for a in assets if a["asset_type"] == "gold"]
    real = round(sum(a["value_inr"] for a in gold_assets), 2)
    total_grams = round(sum((a.get("quantity") or 0) for a in gold_assets), 2)
    holdings = [{"name": a.get("name", "Gold"), "grams": a.get("quantity"),
                 "purity": (a.get("meta") or {}).get("purity"),
                 "rate_per_g": a.get("unit_price"), "value": a.get("value_inr", 0), "as_of": a.get("as_of")}
                for a in gold_assets]
    priced_at = next((a.get("as_of") for a in gold_assets if a.get("as_of")), None)
    active = [l for l in loans
              if (l.get("category") or "").lower().startswith("gold")
              and not l["closed"] and l["balance_inr"] > 0]
    loan_pledged = round(sum(l["balance_inr"] for l in active), 2)
    external = round(settings.gold_external_pledged_inr or 0.0, 2)
    pledged = round(loan_pledged + external, 2)
    return {
        "ltv": ltv,
        "real_value": real,
        "total_grams": total_grams,
        "holdings": holdings,          # per-item grams / purity / live ₹per-g / value / as-of
        "priced_at": priced_at,        # when the live gold rate was last captured
        "gold_loans": [{"name": l["name"], "balance": round(l["balance_inr"], 2)} for l in active],
        "loan_pledged": loan_pledged,
        "external_pledged": external,
        "pledged_value": pledged,
        "net_value": round(real - pledged, 2),
        "borrow_room": round(max(0.0, real * ltv - pledged), 2),
    }


def compute_networth(db: Session, household_id: int | None = None) -> dict:
    """The ONE honest number: assets + accruing investments − loan balances,
    rolled into categories. Single source of truth — pulls loans from the debt
    module and investments from the recurring module (no re-entry, no double count)."""
    # Local imports avoid import-time cycles; both modules are leaf-ward of this one.
    from api.debt import compute_debt
    from api.recurring import compute_recurring
    from api.sinking import compute_sinking

    q = db.query(Asset).filter(Asset.active.is_(True))
    if household_id is not None:
        q = q.filter(Asset.household_id == household_id)
    asset_rows = q.all()
    assets = [_view(a, db) for a in asset_rows]
    # Same reason as in compute_assets: a disposal deactivates the sold asset, so proceeds still owed
    # would vanish from net worth between sale and settlement. This function keeps its OWN asset query,
    # so it needs the line too — patching only compute_assets left ₹6,50,000 missing from net worth.
    try:
        from services.disposals import outstanding_receivables
        assets.extend(outstanding_receivables(db, household_id))
    except Exception:  # noqa: BLE001 — never let this take down the net-worth page
        logger.exception("could not add disposal receivables")
    db.commit()  # persist any refreshed prices

    rec = compute_recurring(db, household_id)
    debt = compute_debt(db, household_id)
    sinking = compute_sinking(db, household_id)

    cats: dict[str, dict] = {}

    def add(cat: str, name: str, value: float, holder, meta: str):
        c = cats.setdefault(cat, {"category": cat, "value": 0.0, "items": []})
        c["value"] = round(c["value"] + value, 2)
        c["items"].append({"name": name, "value": round(value, 2), "holder": holder, "meta": meta})

    for a in assets:
        add(_ASSET_CATEGORY.get(a["asset_type"], "Other"), a["name"], a["value_inr"], a["holder"], a["priced"])
    for r in rec["investments"]:
        add(r["category"], r["name"], r["current_value"], r["holder"], r["kind"])
    for sf in sinking["funds"]:
        if sf["balance"] > 0:
            add("Cash & Bank", f'{sf["name"]} (sinking)', sf["balance"], sf["holder"], "sinking")

    total_assets = round(sum(a["value_inr"] for a in assets) + rec["total_value"] + sinking["total_balance"], 2)
    liabilities = [
        {"name": l["name"], "value": l["balance_inr"], "holder": l["holder"], "meta": f'{l["rate_pct"]}%'}
        for l in debt["loans"]
        # SERVICED loans are the household's debt on its balance sheet — includes a gold loan in a parent's
        # name that the household services (off FOIR, but a real liability against gold that IS an asset).
        # A truly external loan (serviced_by_household=False, a parent pays it) stays off the balance sheet.
        if not l["closed"] and l["balance_inr"] > 0 and l.get("serviced_by_household", True)
    ]
    total_liabilities = round(sum(x["value"] for x in liabilities), 2)
    net_worth = round(total_assets - total_liabilities, 2)

    order = ["Cash & Bank", "Market", "Retirement", "Deposits", "Gold", "Vehicles", "Property", "Other"]
    categories = sorted(cats.values(), key=lambda c: order.index(c["category"]) if c["category"] in order else 99)

    return {
        "net_worth": net_worth,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "categories": categories,
        "liabilities": liabilities,
        "gold_collateral": _gold_collateral(assets, debt["loans"]),
    }


@router.get("/networth/summary")
def networth_summary(db: Session = Depends(get_db),
                     household: Household = Depends(get_current_household)):
    return compute_networth(db, household.id)


# The Wealth tab groups holdings by NATURE, not by the table or account they sit in. `live_priced`
# is a claim about the number itself — it is what estimated_pct counts — so it belongs to the bucket.
_BUCKETS = (
    ("property",       "Property",          "illiquid",           False),
    ("market_exposed", "Market-exposed",    "funds, stock, gold", True),
    ("locked",         "Locked & accruing", "EPF, PPF, RD, FD",   False),
    ("cash",           "Cash",              "reachable today",    True),
    ("depreciating",   "Depreciating",      "losing value",       False),
)

_ASSET_BUCKET = {"property": "property", "stock": "market_exposed", "gold": "market_exposed",
                 "cash": "cash", "vehicle": "depreciating"}


def compute_composition(db: Session, household_id: int | None = None) -> dict:
    """The five buckets behind the Wealth tab: what the household owns by NATURE.

    Every number here is READ from the module that owns it — compute_networth for the headline,
    compute_assets for the asset rows, compute_recurring and compute_sinking for the rest. None of
    it is re-derived from SQL: a second source for the same figure is exactly how the net-worth
    endpoints drifted by Rs 6,50,000 once already.

    Membership is derived, never listed. A fund is market-exposed only once a NAV has actually
    resolved — without one its value is an assumed rate, which puts it on the same footing as an
    EPF — and an asset type nobody prices lands in the not-live-priced bucket rather than inflating
    the share of the balance sheet presented as observed."""
    from api.recurring import compute_recurring
    from api.sinking import compute_sinking

    summary = compute_networth(db, household_id)
    asts = compute_assets(db, household_id)
    rec = compute_recurring(db, household_id)
    sinking = compute_sinking(db, household_id)

    values = {key: 0.0 for key, *_ in _BUCKETS}
    counts = {key: 0 for key, *_ in _BUCKETS}

    def put(key: str, value: float) -> None:
        values[key] += value
        counts[key] += 1

    for a in asts["assets"]:
        put(_ASSET_BUCKET.get(a["asset_type"], "locked"), a["value_inr"])
    for r in rec["investments"]:
        put("market_exposed" if r.get("priced") == "live" else "locked", r["current_value"])
    # compute_networth totals every fund's balance but only lists the positive ones, so the sum has
    # to come from total_balance or the buckets stop adding up to total assets.
    values["cash"] += sinking["total_balance"]
    counts["cash"] += len([f for f in sinking["funds"] if f["balance"]])

    total = summary["total_assets"]
    estimated = sum(values[key] for key, _, _, live in _BUCKETS if not live)
    buckets = [{"key": key, "label": label, "sublabel": sublabel, "count": counts[key],
                "value_inr": round(values[key], 2),
                "pct": round(values[key] / total * 100, 1) if total else 0.0,
                "live_priced": live}
               for key, label, sublabel, live in _BUCKETS]
    buckets.sort(key=lambda b: b["value_inr"], reverse=True)
    return {
        "as_of": date.today().isoformat(),
        "net_worth": summary["net_worth"],
        "total_assets": total,
        "total_liabilities": summary["total_liabilities"],
        "estimated_pct": round(estimated / total * 100, 0) if total else 0,
        "buckets": buckets,
    }


@router.get("/networth/composition")
def networth_composition(db: Session = Depends(get_db),
                         household: Household = Depends(get_current_household)):
    return compute_composition(db, household.id)


def _owned_asset(db: Session, asset_id: int, household: Household) -> Asset:
    asset = db.query(Asset).filter(Asset.id == asset_id,
                                   Asset.household_id == household.id).first()
    if not asset:
        raise HTTPException(404, "Asset not found")
    return asset


@router.post("/networth")
def create_asset(body: AssetIn, db: Session = Depends(get_db),
                 household: Household = Depends(get_current_household)):
    asset = Asset(household_id=household.id, **body.model_dump())
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return _view(asset, db, force=True)


@router.put("/networth/{asset_id}")
def update_asset(asset_id: int, body: AssetIn, db: Session = Depends(get_db),
                 household: Household = Depends(get_current_household)):
    asset = _owned_asset(db, asset_id, household)
    for k, v in body.model_dump().items():
        setattr(asset, k, v)
    asset.last_price = None  # force re-price next read
    db.commit()
    db.refresh(asset)
    return _view(asset, db, force=True)


@router.delete("/networth/{asset_id}")
def delete_asset(asset_id: int, db: Session = Depends(get_db),
                 household: Household = Depends(get_current_household)):
    asset = _owned_asset(db, asset_id, household)
    db.delete(asset)
    db.commit()
    return {"deleted": asset_id}


# ---------- disposals (recording a sale) ----------

def _owned_disposal(db: Session, disposal_id: int, household: Household) -> AssetDisposal:
    d = db.query(AssetDisposal).filter(AssetDisposal.id == disposal_id,
                                       AssetDisposal.household_id == household.id).first()
    if not d:
        raise HTTPException(404, "Disposal not found")
    return d


def _holding_before(asset: Asset | None, d: AssetDisposal) -> float | None:
    """What the asset held before this disposal touched it. A correction is judged against that,
    not against the already-reduced remainder, or the same quantity gets rejected twice."""
    if asset is None or asset.quantity is None:
        return None
    if not asset.active:
        return asset.quantity          # a whole-asset sale deactivates without touching quantity
    return asset.quantity + (d.quantity or 0.0)


def _check(*, gross_proceeds: float, disposed_on: date, acquired_on: date | None,
           quantity: float | None, holding: float | None) -> None:
    if gross_proceeds is None or gross_proceeds <= 0:
        raise HTTPException(400, "Gross proceeds must be more than zero — it is what the buyer paid, "
                                 "before costs and before any loan settled out of it.")
    if disposed_on > date.today():
        raise HTTPException(400, "Sale date is in the future. Record a sale once it has happened.")
    if acquired_on is not None and acquired_on > disposed_on:
        raise HTTPException(400, "Bought after it was sold — check the acquisition date.")
    if quantity is not None:
        if quantity <= 0:
            raise HTTPException(400, "Quantity sold must be more than zero. Leave it blank to sell "
                                     "the whole asset.")
        if holding is not None and quantity > holding:
            raise HTTPException(400, f"Cannot sell {quantity} — the asset holds {holding}.")


def _unapply(asset: Asset | None, d: AssetDisposal) -> None:
    """Put back what recording this disposal took off the asset, so it can be re-applied or dropped."""
    if asset is None:
        return
    if not asset.active:
        asset.active = True
    elif d.quantity is not None:
        asset.quantity = (asset.quantity or 0.0) + d.quantity


def _apply(asset: Asset | None, quantity: float | None) -> None:
    """Mirrors services.disposals.record_disposal: a part sale keeps the asset, a full one closes it."""
    if asset is None:
        return
    if quantity is None or (asset.quantity or 0.0) <= quantity:
        asset.active = False
    else:
        asset.quantity = (asset.quantity or 0.0) - quantity


def _disposal_row(d: AssetDisposal) -> dict:
    """The raw stored fields, so the UI can prefill a correction with what was actually entered."""
    return {
        "id": d.id, "asset_id": d.asset_id,
        "disposed_on": d.disposed_on.isoformat(),
        "acquired_on": d.acquired_on.isoformat() if d.acquired_on else None,
        "quantity": d.quantity,
        "gross_proceeds_inr": d.gross_proceeds_inr,
        "costs_inr": d.costs_inr or 0.0,
        "loan_settled_inr": d.loan_settled_inr or 0.0,
        "receivable_inr": d.receivable_inr or 0.0,
        "cost_basis_inr": d.cost_basis_inr,
        "net_received_inr": d.net_received_inr,
        "notes": d.notes,
        "received_inr": d.received_against_receivable_inr,
        "receipts": [{"id": r.id, "received_on": r.received_on.isoformat(),
                      "amount_inr": r.amount_inr, "asset_id": r.asset_id, "notes": r.notes}
                     for r in d.receipts],
    }


@router.post("/assets/{asset_id}/dispose")
def dispose_asset(asset_id: int, body: DisposalIn, db: Session = Depends(get_db),
                  household: Household = Depends(get_current_household)):
    from services.disposals import record_disposal

    asset = _owned_asset(db, asset_id, household)
    _check(gross_proceeds=body.gross_proceeds_inr, disposed_on=body.disposed_on,
           acquired_on=body.acquired_on, quantity=body.quantity, holding=asset.quantity)
    d = record_disposal(db, household_id=household.id, asset_id=asset_id, **body.model_dump())
    db.commit()
    db.refresh(d)
    return _disposal_row(d)


@router.get("/disposals")
def list_disposals(db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)):
    """Every recorded sale with its realised gain and tax estimate. A disposal with no cost basis
    comes back with a null gain and the reason — never a zero that reads as "no tax due"."""
    from services.disposals import realised_gains

    computed = realised_gains(db, household.id)
    stored = db.query(AssetDisposal).filter(AssetDisposal.household_id == household.id).all()
    # Matched on the values realised_gains echoes back rather than on list position, because it
    # orders by date alone and ties between two same-day sales have no defined order.
    pending: dict[tuple, list[AssetDisposal]] = {}
    for d in stored:
        pending.setdefault(
            (d.disposed_on.isoformat(), d.gross_proceeds_inr, d.loan_settled_inr or 0.0,
             d.receivable_inr or 0.0), []).append(d)

    rows = []
    for g in computed["disposals"]:
        key = (g["disposed_on"], g["gross_proceeds_inr"], g["loan_settled_inr"] or 0.0,
               g["receivable_inr"] or 0.0)
        bucket = pending.get(key) or []
        d = bucket.pop(0) if bucket else None
        rows.append({**g, **(_disposal_row(d) if d else {"id": None})})
    return {**computed, "disposals": rows}


@router.patch("/disposals/{disposal_id}")
def update_disposal(disposal_id: int, body: DisposalPatch, db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)):
    d = _owned_disposal(db, disposal_id, household)
    asset = db.query(Asset).filter(Asset.id == d.asset_id,
                                   Asset.household_id == household.id).first()
    patch = body.model_dump(exclude_unset=True)
    merged = {f: patch.get(f, getattr(d, f)) for f in
              ("disposed_on", "gross_proceeds_inr", "acquired_on", "quantity")}
    _check(gross_proceeds=merged["gross_proceeds_inr"], disposed_on=merged["disposed_on"],
           acquired_on=merged["acquired_on"], quantity=merged["quantity"],
           holding=_holding_before(asset, d))

    if "quantity" in patch and asset is not None:
        _unapply(asset, d)
        _apply(asset, patch["quantity"])
        db.add(asset)
    for k, v in patch.items():
        setattr(d, k, v)
    db.add(d)
    db.commit()
    db.refresh(d)
    return _disposal_row(d)


@router.post("/disposals/{disposal_id}/receipts")
def receive_against_disposal(disposal_id: int, body: ReceiptIn, db: Session = Depends(get_db),
                             household: Household = Depends(get_current_household)):
    """Record money landing against a sale receivable. ONE call moves both sides.

    Atomicity is the single db.commit() below: the service only stages its writes, so a failure
    anywhere before this line — a rejected amount, a crash, a lost connection — leaves the whole
    thing unapplied. A half-recorded arrival is therefore not a state the app can be in."""
    from services.disposals import record_receipt

    d = _owned_disposal(db, disposal_id, household)
    if body.received_on > date.today():
        raise HTTPException(400, "That date is in the future. Record money once it has arrived.")
    try:
        r = record_receipt(db, household_id=household.id, disposal_id=d.id,
                           received_on=body.received_on, amount_inr=body.amount_inr,
                           notes=body.notes)
        credited = r.asset_id
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.refresh(d)
    return {**_disposal_row(d), "credited_asset_id": credited}


@router.delete("/disposals/{disposal_id}/receipts/{receipt_id}")
def undo_receipt(disposal_id: int, receipt_id: int, db: Session = Depends(get_db),
                 household: Household = Depends(get_current_household)):
    """Reverse a mis-entered arrival — the receivable goes back up and the cash row back down."""
    from services.disposals import reverse_receipt

    d = _owned_disposal(db, disposal_id, household)
    try:
        reverse_receipt(db, household_id=household.id, disposal_id=d.id, receipt_id=receipt_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    db.refresh(d)
    return _disposal_row(d)


@router.delete("/disposals/{disposal_id}")
def delete_disposal(disposal_id: int, db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)):
    """Deleting a sale that never happened has to give the asset back, or net worth loses it twice."""
    d = _owned_disposal(db, disposal_id, household)
    if d.receipts:
        raise HTTPException(400, f"{len(d.receipts)} payment(s) have been received against this "
                                 f"sale. Undo those first — deleting the sale now would leave the "
                                 f"money on your balance sheet with nothing explaining it.")
    asset = db.query(Asset).filter(Asset.id == d.asset_id,
                                   Asset.household_id == household.id).first()
    _unapply(asset, d)
    if asset is not None:
        db.add(asset)
    db.delete(d)
    db.commit()
    return {"deleted": disposal_id}
