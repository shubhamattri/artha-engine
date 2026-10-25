"""Vehicle valuation by the IRDAI IDV depreciation schedule (the standard used to value cars
in India). Gives a realistic resale value from purchase price + age — not over/under-stated."""

from __future__ import annotations

from datetime import date


def _dep_factor(years: float) -> float:
    if years <= 0.5: return 0.05
    if years <= 1.0: return 0.15
    if years <= 2.0: return 0.20
    if years <= 3.0: return 0.30
    if years <= 4.0: return 0.40
    if years <= 5.0: return 0.50
    return min(0.90, 0.50 + (years - 5.0) * 0.10)   # ~10%/yr beyond 5y, floor 10% of value


def vehicle_value(purchase_price: float, purchase_date: date, today: date) -> dict:
    months = max(0, (today.year - purchase_date.year) * 12 + (today.month - purchase_date.month))
    years = months / 12.0
    dep = _dep_factor(years)
    return {"value": round(purchase_price * (1 - dep), 2),
            "depreciation_pct": round(dep * 100, 1), "age_years": round(years, 1)}


def appreciate(purchase_price: float, purchase_date: date, annual_pct: float, today: date) -> dict:
    """Real-estate value = purchase price compounded at a configurable annual appreciation rate.
    (Estimate — not a live market valuation; tune annual_pct to a local broker quote.)"""
    months = max(0, (today.year - purchase_date.year) * 12 + (today.month - purchase_date.month))
    years = months / 12.0
    return {"value": round(purchase_price * (1 + annual_pct / 100.0) ** years, 2),
            "appreciation_pct": annual_pct, "age_years": round(years, 1)}
