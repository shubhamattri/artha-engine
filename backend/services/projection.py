"""Projected end-of-cycle spend — chosen by backtest, not by argument.

HOW IT WAS CHOSEN. Candidates were scored on a private household ledger with strict no-lookahead:
to score cycle C at day D a method may use only C's rows up to D plus cycles strictly before C,
sampled at several points in every complete cycle and every category. Candidates: naive rate,
hold-floor, historical median, credibility shrinkage, intra-cycle profile curve, empirical ratio
estimator, compound-Poisson frequency-severity, Croston/Syntetos-Boylan (SBA) for intermittent
demand, forecast combination, and lump-split hybrids.

The headline metric was median absolute error, because a single large one-off purchase owns the
mean; a secondary metric was how often a method correctly called whether a category would go over
budget. The old `spent/elapsed*total` formula was beaten by a bootstrap-significant margin; the
hybrid below tied the best alternative on median error with less bias and less machinery, so it was
taken. Leave-one-cycle-out checks confirmed no single cycle carried the result. The ledger is
private, so the backtest and its numbers are not part of this repository.

THE ALGORITHM

    projected = lumps_seen                                   # counted once, never scaled
              + (drip_seen / days_elapsed) * days_total      # the daily habit, extrapolated
              + 0.5 * historical_lump_rate * days_left       # allowance for surprises still to come

Term 1 fixes the old formula's error: a one-off service bill early in the cycle was being multiplied
by days_total/days_elapsed into a much larger "pace". Term 3 fixes the opposite error: every one of the
observed cycles contained lumps, so assuming NO further surprises under-predicts.
The 0.5 damping was fitted on the panel; undamped over-predicts.

NO MERCHANT CLASSIFICATION. A lump is decided purely by SIZE relative to its own category. An earlier
version also required the merchant to be unfamiliar; tested head-to-head it changed no
predictions, so it was removed. That matters because merchant strings are unreliable evidence: the
same fuel purchase shows up under many distinct merchant strings, most of them new each time. Nothing here
can be broken by a misspelt or miscategorised merchant.
"""

from __future__ import annotations

from collections import defaultdict

from config import settings

# Fitted on ONE household's panel, so they are the first thing a second household has to re-tune;
# config keeps that a settings change, not a source edit.
_CONFIG_ALIASES = {"LUMP_FLOOR": "lump_floor_inr", "LUMP_MULTIPLE": "lump_multiple",
                   "SURPRISE_DAMP": "surprise_damp", "WARMUP_DAYS": "projection_warmup_days"}


def __getattr__(name: str):
    if name in _CONFIG_ALIASES:
        return getattr(settings, _CONFIG_ALIASES[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return 0.0 if not s else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0)


def lump_threshold(typical: float) -> float:
    """Above this, a transaction is a one-off lump. Relative to the category because Rs 2,000 is a
    lump in Entertainment and an ordinary evening in Travel."""
    return max(settings.lump_floor_inr, settings.lump_multiple * max(typical, 1.0))


def _is_lump(amount: float, typical: float) -> bool:
    return (amount or 0.0) >= lump_threshold(typical)


def historical_lump_rate(history_rows, days_covered: int, typical: float) -> float:
    """Rupees of LUMP spend per day, from this category's own past. Sizes the allowance for
    surprises that have not landed yet."""
    if days_covered <= 0:
        return 0.0
    thr = lump_threshold(typical)
    return sum(r.amount or 0.0 for r in history_rows if (r.amount or 0.0) >= thr) / days_covered


def category_typicals(rows) -> dict[str, float]:
    """What an ORDINARY transaction costs in each category — the median, which one car purchase
    cannot drag the way a mean can."""
    amounts: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.amount:
            amounts[r.category].append(r.amount)
    return {cat: _median(xs) for cat, xs in amounts.items()}


def split_by_size(rows, typicals: dict[str, float]) -> dict:
    """Split spend that has ALREADY happened into the everyday habit and the one-off purchases.

    `project()` answers "where will this period end up"; this answers "what was that period made
    of", which is the retrospective question and needs no extrapolation. Both decide what a lump is
    through `_is_lump`, so the two can never disagree about a transaction.

    Feed `typicals` from the household's WHOLE history, not only the rows before this period. A
    lookahead would be cheating in a forecast; here it is what makes two months comparable, because
    otherwise the same purchase is everyday in March and a lump in April purely because more data
    arrived in between.
    """
    one_off, everyday, lumps = 0.0, 0.0, []
    for r in rows:
        amount = r.amount or 0.0
        if _is_lump(amount, typicals.get(r.category, 0.0)):
            one_off += amount
            lumps.append(r)
        else:
            everyday += amount
    return {"one_off_inr": round(one_off, 2), "everyday_inr": round(everyday, 2), "lumps": lumps}


def project(current_rows, days_elapsed: int, days_total: int, *, typical: float = 0.0,
            lump_rate: float = 0.0) -> dict:
    days_elapsed = max(1, days_elapsed)
    days_total = max(days_elapsed, days_total)
    days_left = max(0, days_total - days_elapsed)

    lumps = sum((r.amount or 0.0) for r in current_rows if _is_lump(r.amount, typical))
    drip = sum((r.amount or 0.0) for r in current_rows if not _is_lump(r.amount, typical))
    spent = lumps + drip

    drip_projected = (drip / days_elapsed) * days_total
    surprise = settings.surprise_damp * lump_rate * days_left
    projected = round(max(spent, lumps + drip_projected + surprise), 2)

    # The range must BRACKET the estimate or it reads as broken. Low = no further surprises. High is
    # the flat-rate assumption, but the surprise allowance can legitimately push the estimate above
    # it (a category whose history is lumpy), so the ceiling takes whichever is larger.
    low = round(max(spent, lumps + drip_projected), 2)
    high = round(max(spent, spent / days_elapsed * days_total, projected), 2)
    return {
        "projected_inr": projected,
        "projected_low_inr": low,
        "projected_high_inr": high,
        "one_off_inr": round(lumps, 2),
        "recurring_inr": round(drip, 2),
        "surprise_allowance_inr": round(surprise, 2),
        "confidence": ("low" if days_elapsed < settings.projection_warmup_days
                       else ("medium" if days_elapsed < 10 else "high")),
    }


def project_by_category(current_rows, history_rows, *, days_elapsed: int, days_total: int,
                        history_days: int = 0, **_ignored) -> dict[str, dict]:
    """Per-category projection. `history_rows` are this household's earlier spend rows (any period);
    `history_days` is the number of days those rows span, used to turn lump spend into a daily rate.
    Extra kwargs are accepted and ignored so older callers keep working."""
    hist_by_cat: dict[str, list] = defaultdict(list)
    amounts: dict[str, list[float]] = defaultdict(list)
    for r in history_rows:
        hist_by_cat[r.category].append(r)
        if r.amount:
            amounts[r.category].append(r.amount)

    cur_by_cat: dict[str, list] = defaultdict(list)
    for r in current_rows:
        cur_by_cat[r.category].append(r)

    out = {}
    for cat, rows in cur_by_cat.items():
        typical = _median(amounts.get(cat, []))
        rate = historical_lump_rate(hist_by_cat.get(cat, []), history_days, typical)
        out[cat] = project(rows, days_elapsed, days_total, typical=typical, lump_rate=rate)
    return out
