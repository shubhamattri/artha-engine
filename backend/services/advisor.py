"""Ask Artha — a chat-based financial advisor with the household's FULL context.

Builds a complete financial snapshot (income & allocation, every loan with its rate, net worth +
liquid assets by type, EMI/FOIR headroom, this cycle's spend) and hands it to the LLM as a CA /
strategist. The LLM reasons over the numbers but is told never to invent them. It answers the
strategic questions a real advisor would: HOW to afford a house, which EMI to close to free up
income, what to liquidate vs preserve, how to restructure.
"""

from __future__ import annotations


def _emi(principal: float, annual_pct: float, months: int) -> float:
    """Standard reducing-balance EMI. Deterministic — the LLM must NOT compute this itself."""
    from services.loan_math import emi
    return emi(principal, annual_pct, months)


def _emi_per_rupee() -> float:
    """Monthly EMI per ₹1 of home loan — DERIVED from the configured rate/tenure, because a restated
    constant silently disagrees with them the day either one changes."""
    from config import settings
    return _emi(1.0, settings.home_loan_rate * 100.0, settings.home_loan_years * 12)


def __getattr__(name: str):
    # Was a magic 0.00884 here; kept as a live alias so existing call sites read the derived figure.
    if name == "_EMI_PER_RUPEE":
        return _emi_per_rupee()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _emi_reckoner(symbol: str = "₹") -> str:
    """A ready-reckoner of EMI per ₹1,00,000 borrowed. The LLM scales it (× amount/1e5) instead of doing
    loan arithmetic — which it gets wrong (e.g. quoting the per-lakh EMI for a whole ₹10L loan)."""
    rates = [8.0, 9.0, 10.5, 12.0]
    tenors = [(36, "3y"), (60, "5y"), (84, "7y")]
    lines = [f"EMI READY-RECKONER — monthly EMI per {symbol}1,00,000 borrowed (reducing balance):"]
    for rate in rates:
        cells = ", ".join(f"{lbl} {symbol}{_emi(100000, rate, m):,.0f}" for m, lbl in tenors)
        lines.append(f"  • at {rate}% p.a.: {cells}")
    lines.append(f"  → For any loan: EMI = (per-{symbol}1L figure above) × (loan amount ÷ 1,00,000). "
                 f"A {symbol}10,00,000 loan = 10× the per-lakh EMI. NEVER compute an EMI yourself; use this table "
                 "and sanity-check that EMI × months is greater than the principal.")
    return "\n".join(lines)


def snapshot(db, household_id: int | None = None, period: str = "cycle") -> str:
    from services.money_format import household_symbol
    _c = household_symbol(db, household_id)
    from api.budget import compute_budget
    from api.cashflow import compute_cashflow
    from api.debt import compute_debt
    from api.networth import compute_assets

    c = compute_cashflow(db, household_id)
    d = compute_debt(db, household_id)
    nw = compute_assets(db, household_id)
    b = compute_budget(mode="cycle", db=db, household_id=household_id)       # per-card billing cycle (canonical)
    bm = compute_budget(mode="month", db=db, household_id=household_id)      # calendar month (the other lens)
    income = c["income"] or 1

    loans = sorted((l for l in d["loans"] if not l.get("closed") and l.get("balance_inr", 0) > 0),
                   key=lambda l: -l.get("rate_pct", 0))
    from config import settings
    foir_room = max(0.0, settings.foir_cap * income - c["emi"])
    per_rupee = _emi_per_rupee()

    L = []
    L.append(f"MONTHLY INCOME: {_c}{c['income']:,.0f}")
    L.append(f"ALLOCATION: EMIs {_c}{c['emi']:,.0f} ({c['emi']/income*100:.0f}%), fixed {_c}{c['fixed']:,.0f}, "
             f"savings+investing {_c}{c['saving']+c['sinking']:,.0f}, insurance {_c}{c['insurance']:,.0f}, "
             f"free-to-spend {_c}{c['leftover']:,.0f}")
    L.append(f"EMI HEADROOM before the {settings.foir_cap*100:.0f}%-of-income (FOIR) cap: {_c}{foir_room:,.0f}/mo "
             f"(≈ {_c}{foir_room/per_rupee:,.0f} of additional loan you'd qualify for)")
    L.append("LOANS (highest rate first — prepaying these frees the most EMI/interest):")
    for l in loans:
        emi = l.get("emi") or l.get("emi_inr") or 0
        L.append(f"  • {l['name']}: balance {_c}{l['balance_inr']:,.0f} @ {l['rate_pct']}%, EMI {_c}{emi:,.0f}/mo")
    if not loans:
        L.append("  • (no active loans)")
    L.append("NET WORTH / LIQUID ASSETS by type (what you could liquidate): "
             + ", ".join(f"{k} {_c}{v:,.0f}" for k, v in nw.get("by_type", {}).items()))
    cyc = b.get("cycle", {})
    total_budget = sum(i.get("limit_inr", 0) for i in b["items"])
    active = "CALENDAR MONTH" if period == "month" else "BILLING CYCLE"
    L.append(f"ACTIVE LENS: the user is currently viewing the **{active}** lens. When they say 'this "
             f"period / this month / right now', mean THAT lens — but you have both below, so switch if "
             f"the question clearly calls for the other one.")
    L.append("SPEND — two lenses (use the one that fits the question; default to the ACTIVE lens above):")
    L.append(f"  • THIS BILLING CYCLE ({b.get('period_start','?')} → {b.get('period_end','?')}, "
             f"{cyc.get('days_left','?')} days left): {_c}{b['net_spent_inr']:,.0f} spent of "
             f"{_c}{total_budget:,.0f} budgeted ({_c}{total_budget - b['net_spent_inr']:,.0f} still unspent this "
             f"cycle). Top: " + ", ".join(f"{i['category']} {_c}{i['spent_inr']:,.0f}/{_c}{i['limit_inr']:,.0f}"
                                          for i in sorted(b["items"], key=lambda x: -x["spent_inr"])[:6]))
    L.append(f"  • THIS CALENDAR MONTH so far: {_c}{bm['net_spent_inr']:,.0f} spent.")
    # The single most-abused number: `leftover` is income − EMIs − fixed − savings − insurance. It is NOT
    # spare cash lying around — it is the monthly BUDGET that ALL variable spend (groceries/food/travel/
    # shopping) is drawn FROM. The spend above already consumes it. So the honest "can I spend more" figure
    # is pool − already-spent, which can be near-zero or negative even when the pool looks large.
    pool = c["leftover"]
    var_spent = b["net_spent_inr"]                    # variable spend drawn from the pool THIS cycle
    free_now = pool - var_spent
    L.append(f"  • MONTHLY VARIABLE-SPEND POOL: {_c}{pool:,.0f}. THIS IS NOT SPARE CASH — it is the whole "
             f"budget that every rupee of groceries/food/travel/shopping/etc. comes out of. Do NOT call "
             f"it 'free money sitting there'.")
    L.append(f"  • Of that pool, {_c}{var_spent:,.0f} is ALREADY SPENT this cycle → what's ACTUALLY free to "
             f"spend more right now = {_c}{free_now:,.0f}"
             + (" — i.e. you're already OVER the pool for this period, there is effectively nothing free."
                if free_now < 0 else "."))
    L.append("  • A purchase bigger than what's ACTUALLY free ({}{:,.0f}) — or bigger than one month's pool "
             "— is a CAPITAL decision (borrow / liquidate / stagger over months), not a spend decision. "
             "Never green-light a purchase by pointing at the gross pool while ignoring what's already "
             "spent from it.".format(_c, free_now))
    L.append("")
    L.append(_emi_reckoner(_c))
    return "\n".join(L)


_SYSTEM = (
    "You are Artha — the household's personal CA and financial strategist, as sharp as a top private "
    "banker who actually knows their books. Their COMPLETE current financials are below; you already know "
    "them, so DON'T restate the whole picture — go straight to reasoning about the question.\n\n"
    "HOW TO THINK (this is the important part — most advisors are uselessly conservative; you are not):\n"
    "• Default to figuring out HOW to make it work, not whether to refuse. 'Can I buy a car for my "
    "ALWAYS write money with the same currency symbol the context below uses — never assume one. "
    "brother?' → don't say 'that's risky.' Say: a car around <amt> means roughly a <emi> EMI over 5 years "
    "(scale the reckoner), here's where that <emi> comes from each month (name the exact rupee source — free "
    "headroom, or close loan Z to free its EMI, or trim category W), and here's the catch to watch. "
    "Reason it through end-to-end like you would for a friend, out loud.\n"
    "• For any big purchase or 'can I afford X', walk the actual chain: (1) is it a spend or a capital "
    "decision (bigger than monthly free headroom = capital)? (2) if capital — borrow, liquidate, or "
    "stagger? pick one and say why, using their real loan rates and liquid assets; (3) what's the EMI and "
    "does it fit under the FOIR headroom shown; (4) the single trade-off or risk. Be concrete and "
    "numeric, never hand-wavy.\n"
    "• Prefer freeing up money over saying no: closing a high-rate loan, redirecting an existing EMI once "
    "it ends, or staggering a purchase across two cycles are all valid moves — surface them.\n\n"
    "CYCLE vs MONTH: affordability 'this month / right now' is about the BILLING CYCLE (bills hit the "
    "card's cycle window, shown below with days left), not the calendar 1st–31st. Use the cycle lens for "
    "'right now' questions and say which lens you're using if it matters.\n\n"
    "STYLE: talk like a real advisor texting a client — conversational, direct, no corporate hedging, no "
    "'it is worth noting', no headers or bullet dumps in short answers. Match the answer's length to the "
    "question: a quick question gets 2–4 sentences; a real planning question ('what's the EMI, how do I "
    "pay it') gets the full walk-through, as long as it needs, in plain prose. Cite the specific numbers "
    "that matter.\n\n"
    "MONEY THAT ISN'T FREE (critical — do not get this wrong): the 'monthly variable-spend pool' is NOT "
    "spare cash. It is the budget that all everyday spending is drawn from, and most of it is already "
    "spent each cycle. To judge 'can I spend more', use ACTUALLY-FREE = pool − already-spent-this-cycle "
    "(given below), never the gross pool. Never tell someone they 'have <amt> free sitting there' when <amt> is "
    "the pool; and never say 'you're over budget BUT you have plenty free' — those contradict each other, "
    "the overspend IS draining the pool. If actually-free is small or negative, say so plainly and fund "
    "any real purchase as a capital decision (borrow / liquidate / stagger).\n"
    "HARD RULES (never break): Use ONLY the numbers given below — never invent a balance, price, rate, or "
    "income. If you genuinely need a figure that isn't here (e.g. the price of the car they want), ask for "
    "it or state your assumption explicitly ('assuming an 8-lakh car…'). For ANY EMI or loan-cost number you "
    "MUST use the EMI READY-RECKONER below and scale it (per-1L figure × amount÷1,00,000) — never do the loan "
    "arithmetic yourself, and sanity-check EMI×months > principal.\n\n=== WHAT YOU KNOW (context) ===\n"
)


def reply(db, messages: list[dict], household_id: int | None = None, period: str = "cycle") -> str:
    from config import settings
    from services import llm
    if not llm.available():
        return "The AI advisor isn't configured yet (no API key on the server)."
    convo = "\n".join(f"{m.get('role', 'user').upper()}: {m.get('content', '')}" for m in messages[-10:])
    # Ask Artha runs on the reasoning tier (Opus), not the cheap categorization model — it has to think
    # like a real strategist ("can I buy a car for my brother? what's the EMI? how do I pay it?"), which
    # needs both a smarter model and room to reason. `period` = the lens the user is viewing (cycle|month)
    # so 'this period' answers match what's on screen. 60s timeout because Opus is slower than Haiku.
    out = llm.complete(convo + "\n\nASSISTANT:", system=_SYSTEM + snapshot(db, household_id, period),
                       model=settings.llm_model_smart, max_tokens=2000, timeout=60.0)
    return out or "I couldn't generate a response just now — try again."
