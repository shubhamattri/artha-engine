"""Recording a sale, and keeping net worth honest afterwards.

Selling used to mean flipping `active` to false, which made the asset vanish and took the proceeds
with it, so somebody hand-created a cash row to put the money back. That is two manual steps, either
of which can be forgotten, and it leaves no record of what the thing sold for.

Recording a disposal does both automatically: the asset stops counting, and whatever has not yet been
received comes back as a receivable, because money owed to you on a completed sale is still yours.
Cash already received is deliberately NOT re-added here - it is in a bank account, and the statement
pipeline already sees it. Adding it again is the double-count this feature exists to stop.

When that receivable finally lands, `record_receipt` is the ONE action that moves it: it lowers
`receivable_inr`, credits the sale's cash row, and writes a receipt, all in one uncommitted
transaction. Lowering the receivable on its own - which is all the old "Still to be received" field
did - deletes real money from net worth and leaves nothing behind saying it happened.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from models.asset import Asset
from models.disposal import AssetDisposal, DisposalReceipt

# Suffix for the cash row a settled receivable lands in. Deliberately the same "<asset> — <what>"
# shape as the synthetic "— proceeds receivable" line, so the two halves of one sale read as a pair.
PROCEEDS_SUFFIX = " — sale proceeds"


def record_disposal(
    db: Session,
    *,
    household_id: int,
    asset_id: int,
    disposed_on: date,
    gross_proceeds_inr: float,
    acquired_on: date | None = None,
    quantity: float | None = None,
    costs_inr: float = 0.0,
    loan_settled_inr: float = 0.0,
    receivable_inr: float = 0.0,
    cost_basis_inr: float | None = None,
    notes: str | None = None,
) -> AssetDisposal:
    """Record a sale. A whole-asset disposal (quantity None) deactivates the asset; a part sale
    leaves it active and reduces the quantity, because selling 40 of 100 shares is not an exit."""
    asset = db.query(Asset).filter(Asset.id == asset_id,
                                   Asset.household_id == household_id).first()
    if asset is None:
        raise ValueError(f"no asset {asset_id} in household {household_id}")

    d = AssetDisposal(
        household_id=household_id, asset_id=asset_id, disposed_on=disposed_on,
        acquired_on=acquired_on, quantity=quantity, gross_proceeds_inr=gross_proceeds_inr,
        costs_inr=costs_inr, loan_settled_inr=loan_settled_inr,
        receivable_inr=receivable_inr, cost_basis_inr=cost_basis_inr, notes=notes,
    )
    db.add(d)

    if quantity is None or (asset.quantity or 0) <= quantity:
        asset.active = False
    else:
        asset.quantity = (asset.quantity or 0) - quantity
    db.add(asset)
    return d


def outstanding_receivables(db: Session, household_id: int | None) -> list[dict]:
    """Sale proceeds still owed. Shown as assets because a completed sale with money pending is a
    real claim, not a hope."""
    from services.money_format import household_symbol

    q = db.query(AssetDisposal).filter(AssetDisposal.receivable_inr > 0)
    if household_id is not None:
        q = q.filter(AssetDisposal.household_id == household_id)
    _c = household_symbol(db, household_id)
    out = []
    for d in q.all():
        asset = db.query(Asset).filter(Asset.id == d.asset_id).first()
        out.append({
            "name": f"{asset.name if asset else 'Sold asset'} — proceeds receivable",
            "asset_type": "cash",
            "value_inr": round(d.receivable_inr, 2),
            "priced": "computed",
            "holder": asset.holder if asset else None,
            "disposal_id": d.id,
            "note": f"sold {d.disposed_on.isoformat()} for {_c}{d.gross_proceeds_inr:,.0f}",
        })
    return out


def proceeds_row_name(asset: Asset | None) -> str:
    """The cash row a sale's settled proceeds live in. Derived from the asset, never stored, so the
    same sale always resolves to the same row without a second identifier to keep in step."""
    return f"{asset.name if asset else 'Sold asset'}{PROCEEDS_SUFFIX}"


def _credit_proceeds_row(db: Session, *, household_id: int, asset: Asset | None,
                         amount_inr: float) -> Asset:
    """Add the arrived money to the sale's cash row, creating it on the first receipt."""
    name = proceeds_row_name(asset)
    row = db.query(Asset).filter(Asset.household_id == household_id, Asset.name == name,
                                 Asset.asset_type == "cash").first()
    if row is None:
        row = Asset(household_id=household_id, name=name, asset_type="cash",
                    holder=asset.holder if asset else None, currency="INR",
                    manual_value_inr=0.0, active=True,
                    notes="Proceeds of a recorded sale, as they arrive.")
    row.manual_value_inr = round((row.manual_value_inr or 0.0) + amount_inr, 2)
    row.active = True
    db.add(row)
    db.flush()
    return row


def _owned(db: Session, household_id: int, disposal_id: int) -> AssetDisposal:
    d = db.query(AssetDisposal).filter(AssetDisposal.id == disposal_id,
                                       AssetDisposal.household_id == household_id).first()
    if d is None:
        raise ValueError(f"no disposal {disposal_id} in household {household_id}")
    return d


def record_receipt(db: Session, *, household_id: int, disposal_id: int, received_on: date,
                   amount_inr: float, notes: str | None = None) -> DisposalReceipt:
    """Money arriving against a sale receivable: the receivable falls, the sale's cash row rises,
    and a receipt records it. Both sides or neither - nothing is committed here, so a failure part
    way through can only roll back to the state before the call.

    Refuses more than is outstanding rather than clamping: a household typing a number larger than
    the debt is telling you one of the two figures is wrong, and silently absorbing the difference
    would make a data-entry slip look like a settled sale."""
    from services.money_format import household_symbol

    if amount_inr is None or amount_inr <= 0:
        raise ValueError("The amount received must be more than zero.")
    d = _owned(db, household_id, disposal_id)
    outstanding = round(d.receivable_inr or 0.0, 2)
    amount = round(float(amount_inr), 2)
    if amount > outstanding:
        c = household_symbol(db, household_id)
        raise ValueError(
            f"Only {c}{outstanding:,.2f} is still outstanding on this sale, so {c}{amount:,.2f} "
            f"cannot have arrived against it. If the agreed amount was wrong, correct the sale "
            f"instead.")

    asset = db.query(Asset).filter(Asset.id == d.asset_id,
                                   Asset.household_id == household_id).first()
    row = _credit_proceeds_row(db, household_id=household_id, asset=asset, amount_inr=amount)
    d.receivable_inr = round(outstanding - amount, 2)
    db.add(d)
    r = DisposalReceipt(household_id=household_id, disposal_id=d.id, received_on=received_on,
                        amount_inr=amount, asset_id=row.id, notes=notes)
    db.add(r)
    db.flush()
    return r


def reverse_receipt(db: Session, *, household_id: int, disposal_id: int,
                    receipt_id: int) -> AssetDisposal:
    """Undo a mis-entered receipt, both sides again. Without this a typo is unfixable except by
    editing one side by hand, which is the bug record_receipt exists to remove."""
    d = _owned(db, household_id, disposal_id)
    r = db.query(DisposalReceipt).filter(DisposalReceipt.id == receipt_id,
                                         DisposalReceipt.disposal_id == d.id,
                                         DisposalReceipt.household_id == household_id).first()
    if r is None:
        raise ValueError(f"no receipt {receipt_id} on disposal {disposal_id}")

    asset = db.query(Asset).filter(Asset.id == d.asset_id,
                                   Asset.household_id == household_id).first()
    row = db.query(Asset).filter(Asset.household_id == household_id,
                                 Asset.name == proceeds_row_name(asset),
                                 Asset.asset_type == "cash").first()
    if row is not None:
        row.manual_value_inr = round((row.manual_value_inr or 0.0) - r.amount_inr, 2)
        db.add(row)
    d.receivable_inr = round((d.receivable_inr or 0.0) + r.amount_inr, 2)
    db.add(d)
    db.delete(r)
    db.flush()
    return d


def realised_gains(db: Session, household_id: int | None, slab_rate: float = 0.312) -> dict:
    """Every recorded disposal with its gain and estimated tax. Disposals whose cost basis is not
    known come back with nulls and a reason, never a fabricated zero."""
    from services.capital_gains import compute_gain

    CLASS_OF = {"property": "property", "gold": "gold", "vehicle": "other",
                "stock": "equity_listed_in", "cash": "other", "other": "other"}

    q = db.query(AssetDisposal)
    if household_id is not None:
        q = q.filter(AssetDisposal.household_id == household_id)

    rows, total_tax, unknown = [], 0.0, 0
    for d in q.order_by(AssetDisposal.disposed_on).all():
        asset = db.query(Asset).filter(Asset.id == d.asset_id).first()
        cls = CLASS_OF.get(asset.asset_type if asset else "other", "other")
        # A stock priced in a non-INR currency is a foreign share, which is UNLISTED for Indian tax
        # and needs 24 months rather than 12 to turn long-term.
        if cls == "equity_listed_in" and asset is not None and (asset.currency or "INR") != "INR":
            cls = "equity_foreign"
        g = compute_gain(asset_class=cls, gross_proceeds=d.gross_proceeds_inr, costs=d.costs_inr or 0.0,
                         cost_basis=d.cost_basis_inr, acquired_on=d.acquired_on,
                         disposed_on=d.disposed_on, slab_rate=slab_rate)
        if g.tax_inr:
            total_tax += g.tax_inr
        if g.gain_inr is None:
            unknown += 1
        rows.append({
            "asset": asset.name if asset else f"asset {d.asset_id}",
            "asset_class": cls,
            "disposed_on": d.disposed_on.isoformat(),
            "gross_proceeds_inr": d.gross_proceeds_inr,
            "loan_settled_inr": d.loan_settled_inr,
            "net_received_inr": d.net_received_inr,
            "receivable_inr": d.receivable_inr,
            "gain_inr": g.gain_inr,
            "is_long_term": g.is_long_term,
            "months_held": g.months_held,
            "tax_inr": g.tax_inr,
            "method": g.method,
            "why": g.why,
        })
    return {"disposals": rows, "estimated_tax_inr": round(total_tax, 2),
            "unpriced_count": unknown}
