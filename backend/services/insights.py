"""Insights engine — deterministic signal DETECTORS + an LLM NARRATOR.

Mirrors how bank PFM insight engines work. Each detector is a small pure function that emits a
structured Finding (carrying the REAL numbers, computed here) when its condition holds. An optional
LLM pass turns the top findings into specific, prioritised, human advice — it never computes money,
only explains it. With no AI key, the deterministic titles are used as-is.

Add an insight type = add a detector. That's the whole extensibility story.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Finding:
    kind: str                       # projected_breach | surplus | week_pace | anomaly | savings ...
    severity: int                   # 3 high, 2 medium, 1 low
    tone: str                       # negative | warn | accent | positive
    title: str
    detail: str
    category: str | None = None
    amount: float = 0.0
    action: str = ""                # the suggested move
    data: dict = field(default_factory=dict)


def _sym(ctx: dict) -> str:
    """Currency glyph for this household. Defaulted so a detector called with a bare ctx still works."""
    from services.money_format import DEFAULT_SYMBOL
    return ctx.get("symbol") or DEFAULT_SYMBOL


# ── detectors ─ each takes ctx={plan, cash, debt, symbol} and emits Findings ──
def _detect_breaches(ctx: dict) -> list[Finding]:
    plan = ctx["plan"]
    c = _sym(ctx)
    out = []
    for it in plan.get("items", []):
        if it["limit_inr"] <= 0:
            continue
        if it.get("status") == "over":
            over = round(it["spent_inr"] - it["limit_inr"])
            out.append(Finding("projected_breach", 3, "negative",
                               f"{it['category']} is over budget",
                               f"{c}{over:,} over the {c}{it['limit_inr']:,.0f} target, with the cycle not done.",
                               it["category"], over, action="Pause this category for the rest of the cycle."))
        elif it.get("status") == "will_breach":
            proj = it.get("projected_inr", 0)
            over = round(proj - it["limit_inr"])
            lo, hi = it.get("projected_low_inr"), it.get("projected_high_inr")
            one_off = it.get("one_off_inr", 0) or 0
            span = (f" (likely {c}{lo:,.0f}–{c}{hi:,.0f})"
                    if lo and hi and hi - lo > max(500, it["limit_inr"] * 0.05) else "")
            headroom = max(0, round(it["limit_inr"] - it["spent_inr"]))
            # A breach driven by a ONE-OFF is not a spending habit, so "pause the category" is advice
            # the household cannot act on — the money is already committed. Say what's actually true.
            if one_off > 0 and one_off >= over:
                out.append(Finding("projected_breach", 1, "neutral",
                                   f"{it['category']} will breach, but on a one-off",
                                   f"{c}{one_off:,.0f} of the {c}{it['spent_inr']:,.0f} spent is one-time, which is more "
                                   f"than the {c}{over:,} overshoot. Everyday spending here is not the problem.",
                                   it["category"], over,
                                   action="Nothing to change — or raise the target if this recurs."))
            else:
                out.append(Finding("projected_breach", 2, "warn",
                                   f"{it['category']} on pace to breach",
                                   f"{c}{it['spent_inr']:,.0f} spent so far (target {c}{it['limit_inr']:,.0f}); at this rate "
                                   f"you'll finish ~{c}{proj:,.0f}{span} — about {c}{over:,} over.",
                                   it["category"], over,
                                   action=f"Hold it to ~{c}{headroom:,} more this cycle."))
    return out


def _detect_surplus_allocation(ctx: dict) -> list[Finding]:
    """When you're on track to underspend, recommend WHERE the spare goes — using the personal-
    finance priority waterfall over your REAL loans/accounts: high-rate debt first, then investing."""
    plan, cash, debt = ctx["plan"], ctx["cash"], ctx["debt"]
    c = _sym(ctx)
    items = plan.get("items", [])
    projected_total = round(sum(i.get("projected_inr", 0) for i in items))
    leftover = round(cash.get("leftover", 0))   # income − EMIs − fixed − savings − sinking − insurance
    # TRUE surplus = the variable-spend budget you won't use = leftover − projected variable spend.
    spare = round(leftover - projected_total)
    if spare < 1000:        # on pace to use (or overshoot) the budget → nothing spare to allocate
        return []

    loans = sorted([l for l in debt.get("loans", []) if not l.get("closed") and l.get("balance_inr", 0) > 0],
                   key=lambda l: -l.get("rate_pct", 0))
    if loans and loans[0].get("rate_pct", 0) >= 10:
        l = loans[0]
        name = l["name"].split(" Loan")[0].split(" CC")[0]
        return [Finding("surplus_allocation", 2, "positive",
                        f"Spare {c}{spare:,} → your {l['rate_pct']}% debt",
                        f"You're on pace to have ~{c}{spare:,} unspent. Prepaying the {name} "
                        f"({c}{l['balance_inr']:,.0f} @ {l['rate_pct']}%) kills the most interest per unit.",
                        amount=spare, action=f"Prepay {c}{spare:,} to {name} before the cycle closes.")]
    return [Finding("surplus_allocation", 2, "positive",
                    f"{c}{spare:,} spare — make it work",
                    f"No high-rate debt to clear, so route {c}{spare:,} into your SIP / an index fund "
                    "rather than letting it idle in the account.",
                    amount=spare, action="Top up your SIP or move it to investments.")]


def _detect_retrospective(ctx: dict) -> list[Finding]:
    """Where you overran the plan and what to cap next time — stronger as the cycle nears close."""
    plan = ctx["plan"]
    c = _sym(ctx)
    items = plan.get("items", [])
    cyc = plan.get("cycle") or {}
    days_left = cyc.get("days_left", 30)
    over = sorted(((i["category"], round(i["spent_inr"] - i["limit_inr"])) for i in items
                   if i["limit_inr"] > 0 and i["spent_inr"] > i["limit_inr"]), key=lambda x: -x[1])
    if not over:
        return []
    total_over = sum(o[1] for o in over)
    top = over[0]
    wrap = days_left <= 5
    led = f"{top[0]} ({c}{top[1]:,} over)" + (f" and {over[1][0]}" if len(over) > 1 else "")
    return [Finding("retrospective", 2 if wrap else 1, "warn",
                    "Cycle wrap-up" if wrap else "Where the plan slipped",
                    f"You're {c}{total_over:,} over plan this cycle, led by {led}. Capping {top[0]} to its "
                    f"target would be your single biggest win next cycle.",
                    top[0], total_over, action=f"Set a hard cap on {top[0]} next cycle.")]


def _detect_week_pace(ctx: dict) -> list[Finding]:
    plan = ctx["plan"]
    c = _sym(ctx)
    cyc = plan.get("cycle") or {}
    days_total = cyc.get("days_total", 30) or 30
    days_elapsed = cyc.get("days_elapsed", 1) or 1
    spent = round(sum(i["spent_inr"] for i in plan.get("items", [])))
    target = round(sum(i["limit_inr"] for i in plan.get("items", [])))
    if not target:
        return []
    expected = target * days_elapsed / days_total            # the even-pace line
    # Judge on the PROJECTION, not on spend-vs-line. A single lump early in the cycle (a bike service,
    # an annual premium) puts you "ahead of the line" while your habits are perfectly fine, and telling
    # someone to ease off because of a one-time bill is advice they cannot act on.
    projected = round(sum(i.get("projected_inr", i["spent_inr"]) for i in plan.get("items", [])))
    lumps = round(sum(i.get("one_off_inr", 0) for i in plan.get("items", [])))

    if spent > expected * 1.1:
        ahead = round(spent - expected)
        if lumps > ahead * 0.6 and projected <= target:
            # ahead of the line, but it's one-off spend and the cycle still lands on target
            return [Finding("week_pace", 1, "neutral", "Ahead of the line, but it's one-offs",
                            f"{c}{spent:,} by day {days_elapsed}/{days_total} vs an even {c}{expected:,.0f} — "
                            f"{c}{lumps:,} of that is one-time. Your everyday spending is on track to finish "
                            f"at {c}{projected:,} against a {c}{target:,} plan.", amount=lumps)]
        return [Finding("week_pace", 2, "warn", "Spending faster than the plan",
                        f"By day {days_elapsed}/{days_total} you've spent {c}{spent:,}; the even pace is "
                        f"{c}{expected:,.0f} ({c}{ahead:,} ahead). At this rate the cycle finishes at {c}{projected:,} "
                        f"against a {c}{target:,} plan.",
                        amount=ahead, action="Ease off this week to get back on the line.")]
    if spent < expected * 0.7:
        return [Finding("week_pace", 1, "positive", "Comfortably under pace",
                        f"{c}{spent:,} spent by day {days_elapsed} vs an even {c}{expected:,.0f}.",
                        amount=round(expected - spent))]
    return []


def _detect_top_lever(ctx: dict) -> list[Finding]:
    plan = ctx["plan"]
    c = _sym(ctx)
    items = [i for i in plan.get("items", []) if i["spent_inr"] > 0]
    if not items:
        return []
    top = max(items, key=lambda i: i["spent_inr"])
    total = sum(i["spent_inr"] for i in items) or 1
    share = round(top["spent_inr"] / total * 100)
    if share >= 35:
        return [Finding("top_lever", 1, "accent",
                        f"{top['category']} is {share}% of your spend",
                        f"{c}{top['spent_inr']:,.0f} of {c}{total:,.0f}. It's the biggest lever if you want to cut.",
                        top["category"], top["spent_inr"])]
    return []


_DETECTORS = [_detect_breaches, _detect_surplus_allocation, _detect_retrospective,
              _detect_week_pace, _detect_top_lever]


# ── narration ────────────────────────────────────────────────────────────────
def _narrate(findings: list[Finding], cash: dict, symbol: str | None = None) -> list[dict] | None:
    from services import llm
    from services.money_format import DEFAULT_SYMBOL
    if not llm.available() or not findings:
        return None
    import json
    c = symbol or DEFAULT_SYMBOL
    facts = [{"kind": f.kind, "title": f.title, "detail": f.detail, "category": f.category,
              "amount": f.amount, "action": f.action} for f in findings[:6]]
    prompt = (
        f"Household monthly income {c}{cash.get('income', 0):,.0f}, free-to-budget {c}{cash.get('leftover', 0):,.0f}.\n"
        f"Detected facts (numbers are FINAL — do not change them):\n{json.dumps(facts, ensure_ascii=False)}\n\n"
        "Write 3-4 short, specific, prioritised insights for this household. Each: one punchy title "
        "(≤6 words) and one sentence of concrete advice using ONLY the given numbers. Order by impact. "
        'Return JSON: [{"title":"...","body":"...","tone":"negative|warn|accent|positive"}]. JSON only.'
    )
    raw = llm.complete(prompt, max_tokens=700,
                       system="You are Artha, a sharp, kind household-finance coach. Specific, never preachy; never invent numbers.")
    if not raw:
        return None
    try:
        s = raw.strip()
        if s.startswith("```"):
            s = s.strip("`").lstrip("json").strip()
        arr = json.loads(s)
        return [{"title": o.get("title", ""), "body": o.get("body", ""), "tone": o.get("tone", "accent"),
                 "ai": True} for o in arr if o.get("title")]
    except Exception:  # noqa: BLE001
        return None


def generate(db, mode: str = "cycle", household_id: int | None = None) -> dict:
    """Run detectors → rank → narrate over the chosen lens (cycle|month)."""
    from api.budget import compute_budget
    from api.cashflow import compute_cashflow
    from api.debt import compute_debt
    from services.money_format import household_symbol

    today = date.today()
    ctx = {
        "plan": compute_budget(month=f"{today.year:04d}-{today.month:02d}", mode=mode, db=db,
                               household_id=household_id),
        "cash": compute_cashflow(db, household_id),
        "debt": compute_debt(db, household_id),
        "symbol": household_symbol(db, household_id),
    }

    findings: list[Finding] = []
    for detector in _DETECTORS:
        try:
            findings.extend(detector(ctx))
        except Exception:  # noqa: BLE001
            continue
    findings.sort(key=lambda f: -f.severity)

    deterministic = [
        {"title": f.title, "body": f.detail, "action": f.action,
         "tone": f.tone, "category": f.category, "ai": False}
        for f in findings[:6]
    ]

    # AI insights: the LLM composes FRESH, scenario-specific cards each time — but ONLY from the
    # exact figures in the snapshot + detected signals (the grounding), so the numbers stay correct
    # while the insights vary and can surface non-obvious patterns no fixed rule would catch.
    import json

    from services import advisor, llm
    if not llm.available():
        return {"insights": deterministic, "source": "rules"}

    facts = "\n".join(
        f"- {f.title}: {f.detail}" + (f" [suggested: {f.action}]" if f.action else "")
        for f in findings) or "- (no budget-pace signals this cycle)"
    prompt = (
        advisor.snapshot(db, household_id) + "\n\nDETECTED SIGNALS (exact, already computed):\n" + facts + "\n\n"
        "Write 3-5 SHORT insight cards for this household right now. Surface what matters most AND at "
        "least one NON-OBVIOUS pattern from the data above (e.g. a single loan dominating EMIs, a "
        "category trend, an idle-cash or arbitrage opportunity). Use ONLY numbers that appear above, "
        "copied EXACTLY — never invent or recompute a figure. Each card: a punchy title (≤6 words) and "
        "1-2 sentences ending in a concrete next step. Vary the angles each time. "
        'Return JSON only: [{"title":"...","body":"...","tone":"negative|warn|accent|positive"}].'
    )
    raw = llm.complete(prompt, max_tokens=800,
                       system="You are Artha, a sharp household-finance coach. Fresh, specific, grounded; "
                              "you may combine signals, but you NEVER fabricate or alter a number.")
    try:
        s = raw.strip()
        if s.startswith("```"):
            s = s.strip("`").lstrip("json").strip()
        cards = [{"title": o["title"], "body": o.get("body", ""), "tone": o.get("tone", "accent"), "ai": True}
                 for o in json.loads(s) if o.get("title")]
        if cards:
            return {"insights": cards, "source": "ai"}
    except Exception:  # noqa: BLE001
        pass
    return {"insights": deterministic, "source": "rules"}
