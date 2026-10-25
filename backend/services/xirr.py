"""XIRR — money-weighted annualised return (the same metric Excel/Groww call XIRR).

Given a holding's actual cash movements — every contribution as a dated OUTFLOW and the current market
value as a single INFLOW today — XIRR is the single annual rate that makes their present values net to
zero. It's the honest "what did this investment actually return", accounting for WHEN each rupee went
in (a rupee invested 3 years ago has compounded longer than one added last month).

Solved by BISECTION on NPV(r): NPV is monotonic in r for an invest-then-realise series, so bisection
always converges to the unique root — no Newton divergence, no bad initial guess. Returns None when the
inputs can't define a return (need at least one outflow and one inflow with a sign change).
"""

from __future__ import annotations

from datetime import date


def xirr(cashflows: list[tuple[date, float]], *, tol: float = 1e-7, max_iter: int = 240) -> float | None:
    """cashflows = [(date, amount)]; outflows negative, inflows positive. Returns the annual rate as a
    decimal (0.14 = 14%/yr), or None if it can't be solved."""
    flows = [(d, float(a)) for d, a in cashflows if a]
    if len(flows) < 2:
        return None
    if not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None

    t0 = min(d for d, _ in flows)
    years = [(d - t0).days / 365.0 for d, _ in flows]
    amts = [a for _, a in flows]

    def npv(r: float) -> float:
        return sum(a / (1.0 + r) ** t for a, t in zip(amts, years))

    lo, hi = -0.9999, 100.0          # (1+r) must stay > 0; 100 = 10,000%/yr upper bound
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo == 0:
        return lo
    if f_hi == 0:
        return hi
    if f_lo * f_hi > 0:              # no sign change in the bracket → unsolvable/degenerate
        return None

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < tol or (hi - lo) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0
