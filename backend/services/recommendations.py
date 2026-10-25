"""Budget-learning engine — deterministic target recommendations from spending history.

The numbers are OWNED here (never invented by an LLM): for each variable category we take its monthly
actuals, drop "dirty" months (a one-off ≥ THRESHOLD in that category, or the current partial month),
and suggest the **median of the last 6 clean months** with a min–max band. AI may narrate the `why`,
but the figures are final. Also surfaces: a category vs its target (unrealistic/steady/rising/falling/
volatile), brand-new categories that need a target, and near-constant variable spend that should
become a fixed commitment.
"""

from __future__ import annotations

import statistics
from datetime import date

from sqlalchemy.orm import Session

from models.household import Budget
from services.categories import Category, VARIABLE_CATEGORIES
from services.spend_split import THRESHOLD

WINDOW = 6                # clean periods that inform a target
_MIN_MONTHS = 2           # need at least this many clean periods to suggest anything
_BIG_TXN = THRESHOLD / 2  # a purchase this size (₹20k) is "big-ticket"; several in a period = a spike
_VARIABLE = {c.value for c in VARIABLE_CATEGORIES}


def _current_targets(db: Session, household_id: int) -> dict[str, float]:
    """Latest sticky target per category (the most recent month a target was set for it)."""
    out: dict[str, tuple[str, float]] = {}
    for b in db.query(Budget).filter(Budget.household_id == household_id).all():
        cur = out.get(b.category)
        if cur is None or b.month > cur[0]:
            out[b.category] = (b.month, round(b.monthly_limit_inr or 0.0, 2))
    return {k: v[1] for k, v in out.items()}


def _signal(current: float, suggested: float, vals: list[float]) -> tuple[str, float]:
    """A single headline signal for a category. delta = % the suggested target moves off the current one."""
    delta = round((suggested - current) / current * 100, 1) if current > 0 else 0.0
    band_lo, band_hi = min(vals), max(vals)
    if current > 0 and (current < band_lo * 0.9 or current > band_hi * 1.1):
        return "unrealistic", delta
    mean = statistics.fmean(vals)
    cov = (statistics.pstdev(vals) / mean) if mean else 0.0
    if len(vals) >= 3:
        first, last = statistics.fmean(vals[: len(vals) // 2]), statistics.fmean(vals[-(len(vals) // 2):])
        if first > 0:
            drift = (last - first) / first
            if drift >= 0.15:
                return "rising", delta
            if drift <= -0.15:
                return "falling", delta
    if cov >= 0.35:
        return "volatile", delta
    return "steady", delta


def _why(category: str, current: float, suggested: float, band: tuple[float, float],
         signal: str, n: int, symbol: str | None = None) -> str:
    from services.money_format import DEFAULT_SYMBOL
    lo, hi = band
    c = symbol or DEFAULT_SYMBOL
    inr = lambda v: f"{c}{v:,.0f}"
    base = f"Last {n} clean months ran {inr(lo)}–{inr(hi)}, median {inr(suggested)}."
    if signal == "unrealistic" and current > 0:
        rel = "below" if current < suggested else "above"
        return f"Target {inr(current)} is {rel} that range — {base}"
    if signal == "rising":
        return f"{category} is trending up. {base}"
    if signal == "falling":
        return f"{category} is easing off. {base}"
    if signal == "volatile":
        return f"{category} swings a lot month to month. {base}"
    return base


def recommend(db: Session, household_id: int, *, window: int = WINDOW,
              cycle_mode: str = "instrument_cycle") -> dict:
    """Targets from the last `window` clean PERIODS. The period is set by `cycle_mode` so the suggestion
    follows the lens you're viewing: 'instrument_cycle' → median of clean billing CYCLES; 'calendar_month'
    → median of clean months. Uses the shared Money Slice (variable_spend, all sources), so the history
    it learns from is the same money the matrix shows."""
    from services import money_slice
    from services.categories import variable_category_values
    from services.money_format import household_symbol
    today = date.today()
    sym = household_symbol(db, household_id)
    hh_start = money_slice._household_start(db, household_id)
    cur_period = money_slice.current_key(cycle_mode, today, hh_start)
    unit = "month" if cycle_mode == "calendar_month" else "cycle"
    targets = _current_targets(db, household_id)
    # the 10 built-ins + this household's custom budget categories, so recommendations cover them too
    variable = set(variable_category_values(db, household_id))

    # category -> {period: [total, big-ticket-sum]} — same rows as the matrix (variable, incl. account)
    by_cat: dict[str, dict[str, list[float]]] = {}
    for r in money_slice.base_rows(db, household_id):
        if r.category not in variable:
            continue
        k = money_slice.period_key(r, cycle_mode, today, hh_start, db, household_id)
        if not k:
            continue
        cell = by_cat.setdefault(r.category, {}).setdefault(k, [0.0, 0.0])
        amt = r.amount or 0.0
        cell[0] += amt
        if amt >= _BIG_TXN:
            cell[1] += amt

    all_periods = sorted({p for d in by_cat.values() for p in d})
    latest_period = all_periods[-1] if all_periods else cur_period

    recommendations, new_categories, move_to_fixed = [], [], []
    for category, periods in by_cat.items():
        series = sorted((p, round(t, 2), round(o, 2)) for p, (t, o) in periods.items())
        # CLEAN periods: drop the current partial one and any SPIKE period — one where big-ticket spend
        # (txns ≥ ₹20k) totals ≥ ₹40k (catches a single ₹44k one-off AND two ₹30k ones a single-txn test
        # would miss), so neither skews the target/band.
        clean = [(p, tot) for (p, tot, oneoff) in series if p != cur_period and oneoff < THRESHOLD]
        vals = [t for _, t in clean[-window:]]

        if len(vals) >= _MIN_MONTHS:
            suggested = round(statistics.median(vals), 2)
            band = (round(min(vals), 2), round(max(vals), 2))
            current = targets.get(category, 0.0)
            signal, delta = _signal(current, suggested, vals)
            recommendations.append({
                "category": category, "current_target": current, "suggested": suggested,
                "band": [band[0], band[1]], "basis": f"median-{window}-clean-{unit}s",
                "months_used": len(vals), "signal": signal, "delta_pct": delta,
                "why": _why(category, current, suggested, band, signal, len(vals), sym),
                "ai": False,
            })
            mean = statistics.fmean(vals)
            cov = (statistics.pstdev(vals) / mean) if mean else 1.0
            if len(vals) >= 4 and cov < 0.08:
                move_to_fixed.append({"category": category, "avg": round(mean, 2), "cov": round(cov, 3)})

        # NEW category: first seen in the last ~2 data periods and no standing target yet.
        first_period = series[0][0]
        if category not in targets and first_period >= _shift(latest_period, -1) and category != Category.PERSONAL.value:
            seen_vals = [t for _, t, _ in series]
            new_categories.append({
                "category": category, "months_seen": len(series),
                "suggested": round(statistics.median(seen_vals), 2) if seen_vals else 0.0,
            })

    recommendations.sort(key=lambda r: (r["signal"] != "unrealistic", -abs(r["delta_pct"])))
    return {
        # carried so narrate() phrases money in the household's currency without re-querying
        "currency_symbol": sym,
        "generated_for": cur_period, "window": window, "cycle_mode": cycle_mode, "unit": unit,
        "recommendations": recommendations,
        "new_categories": new_categories,
        "move_to_fixed": move_to_fixed,
    }


def _shift(month: str, delta: int) -> str:
    y, m = map(int, month.split("-"))
    idx = (y * 12 + (m - 1)) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def narrate(recs: dict) -> dict:
    """Best-effort: let the LLM rewrite each `why` into one warm, specific sentence — numbers are FINAL.
    Falls back silently to the deterministic `why` strings when the model is unavailable or errors."""
    import json

    from services import llm
    from services.money_format import DEFAULT_SYMBOL
    items = recs.get("recommendations", [])
    if not items or not llm.available():
        return recs
    c = recs.get("currency_symbol") or DEFAULT_SYMBOL
    facts = [{"category": r["category"], "current_target": r["current_target"],
              "suggested": r["suggested"], "band": r["band"], "signal": r["signal"],
              "months_used": r["months_used"]} for r in items]
    prompt = ("Rewrite each recommendation's rationale as ONE sentence (≤ 28 words). Use ONLY these "
              f"numbers — never invent them. Format every amount as a whole number prefixed with {c} and "
              f"grouped thousands separators (e.g. {c}12,345), NEVER with decimals or fractional units. "
              f"Return a JSON array of strings, same order.\n{json.dumps(facts)}")
    out = llm.complete(prompt, system=("You are Artha, a sharp household-finance coach. Direct, warm, "
                                       f"specific, no fluff. Money is always whole units like {c}12,345 — "
                                       "never fractions. Return ONLY a JSON array of sentences."),
                       max_tokens=600, timeout=14.0)
    if not out:
        return recs
    try:
        lines = json.loads(out[out.index("["): out.rindex("]") + 1])
    except (ValueError, json.JSONDecodeError):
        return recs
    for r, line in zip(items, lines):
        if isinstance(line, str) and line.strip():
            r["why"], r["ai"] = line.strip(), True
    return recs
