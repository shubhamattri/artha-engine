"""Cover an over-budget gap WITHOUT a new loan — the "how do I pay for what I overspent" advisor.

A loan is NEVER the first answer. Ranked least-painful first:
  1. Rein in the categories you actually overspent — free money, pure discipline.
  2. Pause this month's DISCRETIONARY SIP — fully reversible, just nudges a goal back a little.
  3. Draw from a sinking fund — that's exactly what they're set aside for.
  4. LAST resort — a short bridge (0%-EMI / overdraft), only if 1-3 can't close it.

Each move shows how much it frees, so a household can stack a couple to cover the gap. Read-only.
"""

from __future__ import annotations

from datetime import date


def recovery_plan(db, household_id: int | None, gap: float) -> dict:
    """`gap` = the REAL cash shortfall (what you spent beyond your actual free cash), NOT how far over
    an arbitrary budget target you are. We then take only what's NEEDED from each source, cheapest first,
    and STOP once the gap is covered — so a ₹10k shortfall never suggests pausing a ₹58k SIP."""
    gap = round(max(0.0, gap or 0.0), 2)
    if gap <= 0:
        return {"gap": 0.0, "options": [], "non_loan_total": 0.0, "covered": True, "shortfall": 0.0}

    from api.budget import compute_budget
    from api.recurring import compute_recurring
    from api.sinking import compute_sinking
    from services.money_format import household_symbol

    today = date.today()
    _c = household_symbol(db, household_id)

    # COVER levers = REAL cash only, cheapest/least-painful first. Money already spent on overspent
    # categories is GONE — it can't cover a gap, so it is NOT a source here (see `prevent` below).
    sources: list[dict] = []

    # 1) Pause this month's DISCRETIONARY SIP — cash that hasn't left yet; resume next month. Carries the
    #    exact SIP ids so the UI can pause them in one tap.
    try:
        rec = compute_recurring(db, household_id)
        sips = [{"id": i["id"], "name": i["name"], "amount": round(i.get("takehome_outflow", 0) or 0)}
                for i in rec.get("investments", []) if (i.get("takehome_outflow", 0) or 0) > 0]
        sip = round(sum(s["amount"] for s in sips), 2)
        if sip > 0:
            sources.append({"kind": "pause_sip", "available": sip, "title": "Skip part of this month's SIP",
                            "sub": "just this month — resumes in full next month",
                            "action": {"type": "skip_sip", "sips": sorted(sips, key=lambda s: -s["amount"])}})
    except Exception:  # noqa: BLE001
        pass

    # 2) Draw from a sinking fund. NOT "cash already set aside" — that is what this comment used to
    #    claim, and api/sinking cannot support it: a fund's balance is opening_balance plus every
    #    monthly set-aside ASSUMED to have been transferred, reconciled against nothing. A fund set up
    #    and never funded looks exactly like one funded every month.
    #    That matters most precisely here, because this is the overspend-recovery path: telling a
    #    household that is already over budget to draw money that was never set aside sends them
    #    further over. The offer stays — it is usually right and the household knows its own transfers
    #    — but it now carries how much of it is assumed, so the UI can say so instead of asserting cash.
    try:
        sink = compute_sinking(db, household_id)
        funds = sorted((f for f in sink.get("funds", []) if (f.get("balance", 0) or 0) > 0),
                       key=lambda f: -(f.get("balance", 0) or 0))
        sink_total = round(sum(round(f["balance"]) for f in funds), 2)
        if sink_total > 0:
            assumed_total = round(sum(float(f.get("assumed_inr", 0) or 0) for f in funds), 2)
            sources.append({"kind": "sinking", "available": sink_total, "assumed_inr": assumed_total,
                            "title": "Draw from a sinking fund",
                            "sub": " · ".join(f"{f['name']} {_c}{round(f['balance']):,}" for f in funds[:3]),
                            "action": {"type": "withdraw", "id": funds[0]["id"]}})
    except Exception:  # noqa: BLE001
        pass

    # Take ONLY what's needed from real cash, cheapest first — stop once the gap is covered.
    options: list[dict] = []
    remaining = gap
    for s in sources:
        if remaining <= 0:
            break
        use = round(min(s["available"], remaining), 2)
        options.append({**s, "amount": use, "available": s["available"], "enough": use >= remaining})
        remaining = round(remaining - use, 2)

    covered = remaining <= 0
    shortfall = round(max(0.0, remaining), 2)
    non_loan_total = round(gap - shortfall, 2)
    if not covered:
        options.append({"kind": "borrow", "amount": shortfall, "available": shortfall,
                        "title": "Short bridge — last resort", "sub": "only what real cash can't cover",
                        "enough": True})

    # PREVENT (not a cash source) — WHERE the overspend came from, so next cycle is easier. This money is
    # already spent; reining it in stops the bleed, it does NOT pay today's gap.
    prevent = None
    try:
        bud = compute_budget(month=f"{today.year:04d}-{today.month:02d}", mode="cycle",
                             db=db, household_id=household_id)
        over = sorted(((i["category"], round(i["spent_inr"] - i["limit_inr"]))
                       for i in bud.get("items", [])
                       if i["limit_inr"] > 0 and i["spent_inr"] > i["limit_inr"]),
                      key=lambda x: -x[1])
        if over:
            prevent = {"total": round(sum(a for _, a in over), 2),
                       "sub": " · ".join(f"{cat} +{_c}{a:,.0f}" for cat, a in over[:3])}
    except Exception:  # noqa: BLE001
        pass

    return {"gap": gap, "options": options, "non_loan_total": non_loan_total,
            "covered": covered, "shortfall": shortfall, "prevent": prevent}
