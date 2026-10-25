"""Reconcile DEFINED recurring commitments against ACTUAL captured debits, per month.

The gap this closes: loan EMIs (Debt tab), SIPs/RDs (Wealth tab), fixed expenses + rent (CashflowLine)
and insurance premiums are all *planned* entities, while captured transactions are the *actual* — and
nothing links them. This answers, for a given month: did my ₹X EMI / ₹48k rent / ₹10k SIP actually go
out? → ✅ paid (matched to a real debit), ⚠️ missing (no matching debit), ➕ extra (a committed-looking
debit that maps to no commitment). Pure, computed on read — no stored state, always reflects live data.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from models.commitment_link import CommitmentLink
from models.household import CashflowLine, Loan
from models.insurance import InsurancePolicy
from models.recurring import RecurringInvestment
from models.transaction import Transaction
from services import learned

# A debit is a COMMITMENT realisation (not everyday variable spend) if it moves money as an EMI, a
# saving or a transfer, or is a fixed housing / insurance outflow. Everyday SPEND (Food, Shopping…)
# is never matched to a commitment.
_COMMITTED_TYPES = {"EMI", "SAVING", "TRANSFER"}
_COMMITTED_CATS = {"Rent / Housing", "Insurance", "Loan EMI", "Savings & Investments", "Internal Transfer"}


def _month_bounds(month: str):
    from datetime import date as _d
    from datetime import timedelta
    y, mo = int(month[:4]), int(month[5:7])
    start = _d(y, mo, 1)
    end = (_d(y + 1, 1, 1) if mo == 12 else _d(y, mo + 1, 1)) - timedelta(days=1)
    return start, end


def _add_months(d, n: int):
    from datetime import date as _d
    total = (d.month - 1) + n
    return _d(d.year + total // 12, total % 12 + 1, min(d.day, 28))


def _live_in_month(start, end, m_start, m_end) -> bool:
    """A commitment is due in [m_start, m_end] only if it had started by month-end and hasn't ended
    before month-start. `start`/`end` = None means unknown/ongoing (treated as always-on)."""
    if start is not None and start > m_end:
        return False
    if end is not None and end < m_start:
        return False
    return True


def list_commitments(db: Session, household_id: int | None = None, month: str | None = None) -> list[dict]:
    """The unified expected-outflow list for a GIVEN MONTH (default: current) — a commitment only counts
    for the months it actually exists: a loan only from its disbursement until it ends, a fixed expense
    only within its start/end, a SIP only from its anchor. No more showing a loan/expense in a month
    before it began (review: 'not considering the loan/fixed-expense start date')."""
    def scope(model):
        q = db.query(model)
        return q.filter(model.household_id == household_id) if household_id is not None else q

    from datetime import date as _date

    from services import loan_obligation
    month = month or _date.today().strftime("%Y-%m")
    m_start, m_end = _month_bounds(month)
    out: list[dict] = []

    for lo in scope(Loan).filter(Loan.closed.is_(False)).all():
        # loan exists from disbursement until disbursement + tenure — outside that it's not a due EMI.
        loan_end = _add_months(lo.disbursement_date, lo.tenure_months or 0)
        if not _live_in_month(lo.disbursement_date, loan_end, m_start, m_end):
            continue
        amt = loan_obligation.monthly_emi(lo, m_end)   # flexi/moratorium-aware, same EMI as Debt + Cashflow
        if amt and amt > 0:
            out.append({"ref": f"loan:{lo.id}", "kind": "EMI", "name": lo.name,
                        "amount": round(amt, 2), "category": "Loan EMI", "types": {"EMI"}})

    for c in scope(CashflowLine).filter(CashflowLine.active.is_(True)).all():
        if not c.amount_inr or c.amount_inr <= 0:
            continue
        if not _live_in_month(getattr(c, "start_date", None), getattr(c, "end_date", None), m_start, m_end):
            continue
        if c.kind == "fixed":
            rent = "rent" in c.label.lower()
            out.append({"ref": f"cashflow:{c.id}", "kind": "Fixed", "name": c.label,
                        "amount": c.amount_inr,
                        "category": "Rent / Housing" if rent else None,
                        "types": set() if rent else {"TRANSFER"}})
        elif c.kind == "saving":
            out.append({"ref": f"cashflow:{c.id}", "kind": "Saving", "name": c.label,
                        "amount": c.amount_inr, "category": "Savings & Investments", "types": {"SAVING"}})

    for r in scope(RecurringInvestment).filter(RecurringInvestment.active.is_(True)).all():
        if r.monthly_contribution and r.monthly_contribution > 0 and r.as_of_date <= m_end:
            out.append({"ref": f"recurring:{r.id}", "kind": "SIP", "name": r.name,
                        "amount": r.monthly_contribution, "category": "Savings & Investments",
                        "types": {"SAVING"}})

    for p in scope(InsurancePolicy).filter(InsurancePolicy.active.is_(True)).all():
        if p.premium and p.premium > 0 and p.premium_frequency == "monthly":
            out.append({"ref": f"insurance:{p.id}", "kind": "Insurance", "name": p.provider,
                        "amount": p.premium, "category": "Insurance", "types": set()})
    return out


def _is_committed_debit(t: Transaction) -> bool:
    return t.txn_type in _COMMITTED_TYPES or t.category in _COMMITTED_CATS


def _compatible(cm: dict, t: Transaction) -> bool:
    """HARD gate: a debit may only match a commitment of the RIGHT kind — an EMI to a loan, a saving to
    a SIP, a housing spend to rent, a person-transfer to a household-help line. This is what stops a
    coincidental same-amount transfer from being matched (and then learned) as a loan EMI."""
    if t.txn_type in cm.get("types", set()):
        return True
    return bool(cm.get("category")) and t.category == cm["category"]


def _payee(t: Transaction) -> str:
    return t.merchant or t.description or ""


def _links(db: Session, household_id: int | None) -> dict[str, str]:
    """{payee-signature: commitment_ref} the system has learned for this household."""
    q = db.query(CommitmentLink)
    if household_id is not None:
        q = q.filter(CommitmentLink.household_id == household_id)
    return {r.signature: r.commitment_ref for r in q.all()}


def _resolve_link(payee_sig: str, links: dict[str, str]) -> str | None:
    """Map a transaction's payee to a CONFIRMED commitment. Exact signature first; then a UNIQUE
    token-subset match — a link the user saved under a short name ('RESHMA') still matches the fuller
    bank payee ('RESHMA AYYUBAKHAN HOKRANI'), and vice-versa. This is what tells the maid from the cook
    even when the bank spells their names differently. Ambiguous (2+ links match) → no match, stays for
    the user to confirm, so we never guess between two people."""
    if not payee_sig:
        return None
    if payee_sig in links:
        return links[payee_sig]
    ptoks = set(payee_sig.split())
    if not ptoks:
        return None
    hits = {ref for sig, ref in links.items()
            if sig and (set(sig.split()) <= ptoks or ptoks <= set(sig.split()))}
    return next(iter(hits)) if len(hits) == 1 else None


def link(db: Session, household_id: int, signature: str, commitment_ref: str, source: str = "user") -> None:
    """Teach (or re-teach) that a payee signature belongs to a commitment. A user confirmation (source=
    'user') always wins over an auto-learned guess."""
    if not signature or not commitment_ref:
        return
    row = (db.query(CommitmentLink)
           .filter(CommitmentLink.household_id == household_id, CommitmentLink.signature == signature)
           .first())
    if row is None:
        db.add(CommitmentLink(household_id=household_id, signature=signature,
                              commitment_ref=commitment_ref, source=source, hits=1))
    elif source == "user" or row.source != "user":   # never let an auto guess overwrite a user's confirmation
        row.commitment_ref = commitment_ref
        row.source = source
        row.hits = (row.hits or 0) + 1
    db.commit()


def _commitment_category(db: Session, ref: str) -> tuple[str, str] | None:
    """The (category, txn_type) a debit REALISING this commitment should carry, so it lands in the right
    bucket and drops OUT of variable spend. Household-help/fixed person transfers → Internal Transfer
    (like the cook); rent → Rent/Housing; loan → Loan EMI; SIP/RD → Savings; insurance → Insurance."""
    try:
        kind, sid = ref.split(":")
        sid = int(sid)
    except (ValueError, AttributeError):
        return None
    if kind == "loan":
        return ("Loan EMI", "EMI")
    if kind == "recurring":
        return ("Savings & Investments", "SAVING")
    if kind == "insurance":
        return ("Insurance", "SPEND")
    if kind == "cashflow":
        c = db.get(CashflowLine, sid)
        if c and "rent" in (c.label or "").lower():
            return ("Rent / Housing", "SPEND")
        return ("Internal Transfer", "TRANSFER")     # cook, maid, help, family support
    return None


def apply_link_categories(db: Session, household_id: int | None = None) -> dict:
    """Push every CONFIRMED identity link down onto its transactions' CATEGORY. A payment you've told us
    is the maid/cook/rent/EMI must not sit in a variable-spend bucket ('Personal / Misc') inflating your
    budget — it belongs in its committed category (and out of the Plan). Runs on every sync so it
    self-heals, and only touches rows whose category is currently wrong. Locks them (confidence='high')
    so the AI pass can't undo it. This is what makes it work NEXT TIME, not just a one-off backfill."""
    links = _links(db, household_id)
    if not links:
        return {"ok": True, "recategorized": 0}
    q = db.query(Transaction).filter(Transaction.direction == "debit", Transaction.archived.is_(False))
    if household_id is not None:
        q = q.filter(Transaction.household_id == household_id)
    changed = 0
    from services.audit import log_event
    for t in q.all():
        ref = _resolve_link(learned.signature(_payee(t)), links)
        if not ref:
            continue
        ct = _commitment_category(db, ref)
        if not ct:
            continue
        cat, ttype = ct
        if t.category != cat or t.txn_type != ttype:
            log_event(db, household_id=t.household_id, txn_id=t.id, action="recategorize",
                      field="category", old=t.category, new=cat, actor="commitment-link")
            t.category, t.txn_type, t.confidence = cat, ttype, "high"
            changed += 1
    db.commit()
    return {"ok": True, "recategorized": changed}


def unlink(db: Session, household_id: int, signature: str) -> bool:
    """Forget a payee→commitment link (the user linked the wrong debit). Next reconcile stops matching
    that payee, so the commitment goes back to 'missing' until re-confirmed."""
    row = (db.query(CommitmentLink)
           .filter(CommitmentLink.household_id == household_id, CommitmentLink.signature == signature)
           .first())
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def candidates(db: Session, household_id: int | None, ref: str, month: str) -> dict:
    """The debits this month that could pay commitment `ref` — the pool the exception queue offers when a
    commitment is 'missing' (e.g. a household member's pocket money or a parent's support: person-
    transfers the system won't auto-match by amount, so you confirm which one is theirs — once — and
    every future payment to that payee reconciles). Only right-KIND debits (a transfer for a person
    line, an EMI for a loan), never ones already confirmed for a DIFFERENT commitment. Ranked by
    amount closeness. Read-only."""
    items = list_commitments(db, household_id, month)
    cm = next((c for c in items if c["ref"] == ref), None)
    if cm is None:
        return {"ref": ref, "candidates": []}
    links = _links(db, household_id)
    q = db.query(Transaction).filter(
        Transaction.date.like(f"{month}%"), Transaction.direction == "debit",
        Transaction.archived.is_(False), Transaction.dup_of_id.is_(None))
    if household_id is not None:
        q = q.filter(Transaction.household_id == household_id)
    out = []
    for t in q.all():
        sig = learned.signature(_payee(t))
        linked = _resolve_link(sig, links)
        if linked and linked != ref:      # already belongs to someone else — don't offer it
            continue
        if not _compatible(cm, t):        # wrong kind (a spend can't be a person-transfer commitment)
            continue
        out.append({"txn_id": t.id, "amount": round(t.amount, 2), "payee": _payee(t),
                    "date": t.date, "category": t.category, "signature": sig,
                    "linked_here": linked == ref})
    out.sort(key=lambda c: abs(c["amount"] - cm["amount"]))
    return {"ref": ref, "name": cm["name"], "amount": cm["amount"], "candidates": out[:12]}


def reconcile(db: Session, household_id: int | None, month: str) -> dict:
    """month = 'YYYY-MM'. Match each commitment to at most one committed debit (1:1):
      1. IDENTITY — a debit whose learned payee signature points at a commitment matches it regardless
         of amount (tells the cook from the maid at the same ₹7,000; links a first/last EMI).
      2. AMOUNT — remaining commitments match a remaining debit of ~the right size (bootstrap; these
         seed the identity store via learn()).
    Unmatched commitments → 'missing'; unmatched recurring-looking debits → 'extra'. Read-only."""
    commitments = list_commitments(db, household_id, month)   # month-aware — only what's due THIS month
    by_ref = {c["ref"]: c for c in commitments}
    q = db.query(Transaction).filter(
        Transaction.date.like(f"{month}%"), Transaction.direction == "debit",
        Transaction.archived.is_(False), Transaction.dup_of_id.is_(None))
    if household_id is not None:
        q = q.filter(Transaction.household_id == household_id)
    links = _links(db, household_id)
    # A debit is eligible if it looks like a commitment (EMI/saving/transfer/rent/insurance) OR its payee
    # is CONFIRMED-linked to a commitment. The second half is critical: the maid's ₹7,000 is categorised
    # 'Personal / Misc' (SPEND), so without this it would be filtered out before identity matching ever
    # runs — and she'd wrongly show as MISSING while her real debit sits in the ledger.
    debits = [t for t in q.all()
              if _is_committed_debit(t) or _resolve_link(learned.signature(_payee(t)), links)]

    used_txn: set[int] = set()
    matched_ref: dict[str, tuple[Transaction, str]] = {}   # ref -> (txn, via)

    # ── Phase 1: IDENTITY (confirmed payee → commitment), amount-independent ──
    for t in debits:
        ref = _resolve_link(learned.signature(_payee(t)), links)
        if ref and ref in by_ref and ref not in matched_ref:
            matched_ref[ref] = (t, "identity")
            used_txn.add(t.id)

    # ── Phase 2: AMOUNT bootstrap for still-unmatched commitments (biggest first, most distinctive) ──
    #    A debit is a candidate ONLY if it's the right KIND (_compatible) — an EMI debit for a loan, a
    #    saving for a SIP, a transfer for household help. Category is a HARD gate, not a tiebreaker, so a
    #    coincidental same-amount transfer can never be matched (and then learned) as a loan EMI.
    for cm in sorted(commitments, key=lambda c: -c["amount"]):
        if cm["ref"] in matched_ref:
            continue
        # PERSON payments (cook, maid, family support — a {TRANSFER}-only commitment) match ONLY by a
        # confirmed identity, never by amount: you pay many people, and a coincidental same-amount
        # transfer (a one-off lawyer fee ≈ the ₹6,000 school line) must never be shown as that
        # commitment. They stay 'missing' until you confirm who pays them. EMIs/SIPs/rent/insurance —
        # distinctive amounts or their own category — keep matching by amount.
        if cm.get("types") == {"TRANSFER"}:
            continue
        tol = max(200.0, cm["amount"] * 0.02)
        best = None
        for t in debits:
            if t.id in used_txn or not _compatible(cm, t) or abs(t.amount - cm["amount"]) > tol:
                continue
            if best is None or abs(t.amount - cm["amount"]) < abs(best.amount - cm["amount"]):
                best = t
        if best is not None:
            matched_ref[cm["ref"]] = (best, "amount")
            used_txn.add(best.id)

    paid, missing = [], []
    for cm in commitments:
        hit = matched_ref.get(cm["ref"])
        if hit:
            t, via = hit
            paid.append({**cm, "actual": round(t.amount, 2), "txn_id": t.id,
                         "txn_date": t.date, "payee": _payee(t), "via": via,
                         "signature": learned.signature(_payee(t))})   # so a wrong link can be undone
        else:
            missing.append(cm)

    # 'extra' = an unmatched debit that LOOKS like a recurring commitment not yet defined (an EMI, a
    # SIP/saving, a rent or insurance outflow). Plain person-to-person transfers are money movement.
    extra = [{"txn_id": t.id, "amount": round(t.amount, 2), "category": t.category,
              "payee": _payee(t), "date": t.date, "signature": learned.signature(_payee(t))}
             for t in debits if t.id not in used_txn
             and (t.txn_type in ("EMI", "SAVING") or t.category in ("Rent / Housing", "Insurance"))]
    return {
        "month": month,
        "paid": paid, "missing": missing, "extra": extra,
        "expected_total": round(sum(c["amount"] for c in commitments), 2),
        # paid_total = what ACTUALLY went out (real debit amounts), not the planned figure — so a first/
        # broken-period EMI or an off-amount payment shows the truth (review #4).
        "paid_total": round(sum(p["actual"] for p in paid), 2),
        "paid_planned_total": round(sum(p["amount"] for p in paid), 2),
        "missing_total": round(sum(m["amount"] for m in missing), 2),
        "extra_total": round(sum(e["amount"] for e in extra), 2),
    }



def tag_loan_emis(db, household_id: int, *, dry_run: bool = False) -> dict:
    """A debit whose amount is exactly a tracked loan's EMI is that EMI, whatever the narration says.

    Bank debit ADVICE is not the same as an EMI reminder. The reminder says "EMI of Rs 8400 for ICICI
    Bank Education Loan" and classifies correctly; the advice says "InfoBIL*Education", which matched
    the word Education and became variable spending. Rs 8,400 of loan repayment landed in the same
    bucket as school fees, and the same happened at Rs 13,000.

    The loan table already holds the exact EMI, so match on it. An amount equal to a real obligation
    is conclusive in a way that merchant text is not — that is the whole point of tracking the loan.
    """
    from models.transaction import Transaction

    # Ask the debt engine rather than recomputing. A plain amortisation formula is wrong for a gold
    # loan (interest-only, so Rs 8,000 not Rs 85,000) and wrong for a loan in moratorium (Rs 8,400
    # not Rs 23,000). Recomputing here would have produced three numbers that match nothing the user
    # has ever seen, and silently tagged the wrong transactions.
    from api.debt import compute_debt

    loans = [l for l in compute_debt(db, household_id)["loans"]
             if not l.get("closed") and l.get("emi_inr")]
    # {rounded emi: loan name}. Round amounts are skipped: a Rs 10,000 EMI cannot be told apart from
    # any other Rs 10,000 debit, and wrongly hiding real spending is worse than missing one EMI.
    by_amt: dict[int, str] = {}
    for loan in loans:
        r = round(loan["emi_inr"])
        if r % 100 == 0:
            continue
        by_amt.setdefault(r, loan["name"])

    if not by_amt:
        return {"tagged": 0, "by_loan": {}}

    rows = (db.query(Transaction)
            .filter(Transaction.household_id == household_id, Transaction.direction == "debit")
            .filter(Transaction.txn_type.in_(["SPEND", "FEE"]) | Transaction.txn_type.is_(None))
            .filter(Transaction.amount.in_(list(by_amt))).all())
    by_loan: dict[str, int] = {}
    for t in rows:
        name = by_amt.get(round(t.amount))
        if not name:
            continue
        if not dry_run:
            t.txn_type = "EMI"
            t.category = "Loan EMI"
        by_loan[name] = by_loan.get(name, 0) + 1
    if not dry_run:
        db.commit()
    return {"tagged": sum(by_loan.values()), "by_loan": by_loan, "dry_run": dry_run}
