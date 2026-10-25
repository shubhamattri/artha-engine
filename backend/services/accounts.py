"""Canonical account/card identity.

A physical card or bank account is identified by its **last-4**, NOT by the free-text `bank`
string — which arrives spelled differently per source (statement "HDFC", Gmail alert "Hdfcbank",
SMS "SMS"). Without this, the same card ••7654 shows up as three "accounts" in every picker.

This module collapses each last-4 to ONE clean (bank, card_type) label, preferring the
statement-sourced rows (the authoritative product name like "Platinum" / "Select RuPay"),
and ignoring the source artefacts ("Alert", "SMS", "Manual"). Used by the facets endpoint, the
"Paid from" pickers, the SMS/alert ingest (so new captures join the real card), and the backfill.
"""

from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from models.transaction import Transaction

PLACEHOLDER_LAST4 = {"", "0000", None}
# Zero-padded last-4s an earlier extractor invented from short masks. Not real cards; rows still
# carry them, so the repair pass must keep looking for them until those rows are all re-attributed.
LEGACY_ARTEFACT_LAST4 = {"0179"}
# card_type values that are ingest artefacts, never the real product name
JUNK_TYPES = {"Alert", "SMS", "Manual", ""}
_BANK_ALIASES = {
    "hdfc": "HDFC", "hdfcbank": "HDFC", "hdfc bank": "HDFC",
    "icici": "ICICI", "icici bank": "ICICI", "icicibank": "ICICI",
    "axis": "Axis", "axisbk": "Axis", "axis bank": "Axis",
    "kotak": "Kotak", "kotakbank": "Kotak", "kotak bank": "Kotak",
}


def normalize_bank(b: str | None) -> str:
    if not b:
        return ""
    low = b.strip().lower()
    if low in _BANK_ALIASES:
        return _BANK_ALIASES[low]
    # substring fallback: a garbled bank ("Axis Select RuPay ••7788") still contains its real bank name.
    for key, clean in (("hdfc", "HDFC"), ("icici", "ICICI"), ("axis", "Axis"), ("kotak", "Kotak")):
        if key in low:
            return clean
    return b.strip().title()


def _real_banks(db: Session, household_id: int | None = None) -> set[str]:
    """Bank names this household actually has, from the accounts table.

    Was a hardcoded set of four, which silently did nothing for any household banking elsewhere —
    the repair would leave their garbled bank field untouched with no error. Falls back to the banks
    already present in the ledger so a household that has not registered anything still self-heals.
    """
    from models.account import Account
    q = db.query(Account.bank).filter(Account.bank.isnot(None))
    if household_id is not None:
        q = q.filter(Account.household_id == household_id)
    banks = {b for (b,) in q.distinct().all() if b}
    if banks:
        return banks
    tq = db.query(Transaction.bank).filter(Transaction.bank.isnot(None))
    if household_id is not None:
        tq = tq.filter(Transaction.household_id == household_id)
    return {b for (b,) in tq.distinct().all() if b and b.lower() not in ("sms", "manual")}


def repair_banks(db: Session, household_id: int, *, dry_run: bool = False) -> dict:
    """Reproducible backfill: rewrite a GARBLED `bank` field to its real bank (e.g. 'Axis Select RuPay
    ••7788' → 'Axis') so a card's billing cycle resolves correctly. Only touches rows that resolve to a
    real bank — an unidentifiable SMS orphan is left alone (never cosmetically re-cased). Runs on sync so
    bad data self-heals; idempotent. dry_run returns the diff without writing."""
    real_banks = _real_banks(db, household_id)
    rows = db.query(Transaction).filter(Transaction.household_id == household_id).all()
    changed = 0
    moves: dict[str, int] = {}
    for r in rows:
        clean = normalize_bank(r.bank)
        if clean in real_banks and clean != (r.bank or ""):
            moves[f"{r.bank} → {clean}"] = moves.get(f"{r.bank} → {clean}", 0) + 1
            changed += 1
            if not dry_run:
                r.bank = clean
    if not dry_run:
        db.commit()
    return {"changed": changed, "moves": moves, "dry_run": dry_run}


def canonical_by_last4(db: Session, household_id: int | None = None) -> dict[str, tuple[str, str]]:
    """last4 → (bank, card_type): the dominant REAL label for each physical card.

    Priority per last4: most-used statement-sourced (bank, non-junk type) → most-used non-junk
    of any source → most-used anything. Bank name is normalized. Pass household_id to scope to one
    household (None = all, for the ingest/resolve path that has no household context yet)."""
    q = db.query(Transaction.card_last4, Transaction.bank, Transaction.card_type,
                 Transaction.source, func.count().label("n"))
    if household_id is not None:
        q = q.filter(Transaction.household_id == household_id)
    rows = (
        q.group_by(Transaction.card_last4, Transaction.bank, Transaction.card_type, Transaction.source)
        .all()
    )
    by_last4: dict[str, list] = defaultdict(list)
    for last4, bank, ctype, source, n in rows:
        if last4 in PLACEHOLDER_LAST4:
            continue
        by_last4[last4].append((bank, ctype, source, n))

    out: dict[str, tuple[str, str]] = {}
    for last4, cands in by_last4.items():
        def best(pred):
            picks = [(n, normalize_bank(b), ct) for (b, ct, s, n) in cands if pred(b, ct, s)]
            return max(picks, key=lambda t: t[0]) if picks else None

        pick = (best(lambda b, ct, s: s == "statement" and ct not in JUNK_TYPES)
                or best(lambda b, ct, s: ct not in JUNK_TYPES)
                or best(lambda b, ct, s: True))
        if pick:
            out[last4] = (pick[1], pick[2])
    return out


def card_list(db: Session, household_id: int | None = None) -> list[dict]:
    """One clean entry per physical card — for the Card filter and "Paid from" pickers."""
    canon = canonical_by_last4(db, household_id=household_id)
    cards = [
        {"bank": bank, "card_type": ctype, "card_last4": last4,
         "label": f"{bank} {ctype} ••{last4}".strip()}
        for last4, (bank, ctype) in canon.items()
    ]
    return sorted(cards, key=lambda c: (c["bank"], c["card_last4"]))


def resolve_card(db: Session, last4: str | None,
                 household_id: int | None = None) -> tuple[str, str] | None:
    """For SMS/alert ingest: map a captured last4 onto the real (bank, card_type).

    History first — the dominant label across existing transactions is the best evidence of what a
    card really is. But history cannot know about a card that has never been seen, which is exactly
    the case right after someone adds one in the UI: the alert was captured, yet the row was filed
    against the phantom "SMS / Account / 0000" bucket instead of the account they just registered.
    So fall back to the accounts table, which is the household telling us directly.
    """
    if not last4 or last4 in PLACEHOLDER_LAST4:
        return None
    hit = canonical_by_last4(db).get(last4)
    if hit:
        return hit
    try:
        from models.account import Account
        q = db.query(Account).filter(Account.last4 == last4, Account.active.is_(True))
        if household_id is not None:
            q = q.filter(Account.household_id == household_id)
        a = q.first()
        if a and a.bank:
            # label mirrors what statements use, so a later statement import merges rather than forks
            return (a.bank, a.label or ("Credit Card" if a.kind == "card" else "Account"))
    except Exception:  # noqa: BLE001 — table absent on a pre-041 install
        pass
    return None


def excluded_last4(db: Session, household_id: int | None = None) -> set[str]:
    """Card numbers this household has deliberately switched off.

    Excluding a card used to mean editing a list of real card numbers in config.py, which put one
    household's data in every install and needed a developer for a user decision. It is now an
    account registered with active=False, so the user does it in Settings. The old setting is still
    honoured so an existing .env keeps working.
    """
    from config import settings
    from models.account import Account
    out = {str(x) for x in (getattr(settings, "excluded_card_last4", None) or [])}
    q = db.query(Account.last4).filter(Account.active.is_(False))
    if household_id is not None:
        q = q.filter(Account.household_id == household_id)
    return out | {l4 for (l4,) in q.all() if l4}


# "...debited from your account ending 5566...", "A/c XX5566", "Card no. XX7788", "a/c no XXXXXX5566"
_LAST4_IN_TEXT = re.compile(
    r"(?:account|acct|a/c|ac|card)\s*(?:no\.?|number|ending|ending\s+in|xx+)?[\s:.\-]*"
    r"(?:x|\*|•)*(\d{4})\b",
    re.IGNORECASE,
)


def last4s_mentioned(text: str | None) -> set[str]:
    """Every account/card last-4 named anywhere in an alert's text.

    The exclusion check used to run only against the last4 the extractor produced. When extraction
    missed it — HDFC InstaAlerts state it in prose ("debited from your account ending 5566") rather
    than a field — the row was stored as "0000" and compared "0000" against the exclusion list, so an
    excluded account sailed through. Reading the raw text means a guard cannot be defeated by a
    parsing miss, which is how a non-household account's UPI transfers reached the ledger.
    """
    if not text:
        return set()
    return {m.group(1) for m in _LAST4_IN_TEXT.finditer(text)}


def is_excluded_text(text: str | None, excluded: set[str]) -> str | None:
    """The excluded last4 this text refers to, if any. Returns the number so it can be logged."""
    if not excluded:
        return None
    return next((l4 for l4 in last4s_mentioned(text) if l4 in excluded), None)


# Banks mask account numbers inconsistently: "XX3456", "XX456", "4xxx7654", "...4433", "xxxxxxxxxx3456".
# Capture the digit run after an account/card word regardless of how many digits survive the mask.
# Grab the whole masked TOKEN after the account word, then take its trailing digits. Matching digits
# directly fails on the shapes banks actually use: "Acc" is caught by an "ac" alternative that then
# chokes on the third c, and "4xxx7654" interleaves digits with the mask so a plain \d run stops at
# the leading 4. \b after the keyword, and a token-then-digits split, handle both.
_MASKED_NUM = re.compile(
    r"(?:account|acct|acc|a/c|ac|card)\b\s*(?:no\.?|number|ending(?:\s+in)?)?[\s:.\-]*"
    r"([0-9xX*•.]{3,20})",
    re.IGNORECASE,
)


def _trailing_digits(token: str) -> str:
    """Digits at the end of a masked token: "XX456"->456, "4xxx7654"->7654, "...4433"->4433."""
    m = re.search(r"(\d+)\D*$", token)
    return m.group(1) if m else ""


def resolve_last4_from_text(text: str | None, registered, bank_hint: str | None = None) -> str | None:
    """Match a masked account number in an alert against the household's registered accounts.

    ICICI writes "ICICI Bank Acc XX456" — three digits, not four. Taking the last four CHARACTERS of
    that gives "X456", which cleans to a zero-padded number no card has. Dozens of real transactions
    (home loan EMIs, education loan, groceries) ended up filed under that phantom.

    Matching by SUFFIX against accounts the household actually registered fixes the cause. A partial
    can still be ambiguous — "456" matches both ICICI 3456 and HDFC 8456 — so the sender's bank is
    used to narrow it, and anything still ambiguous returns None rather than guessing. Attributing a
    transaction to the wrong account is worse than leaving it unattributed.

    `registered` may be a set of last4s or a {last4: bank} mapping; the mapping enables bank_hint.
    """
    if not text or not registered:
        return None
    by_bank = registered if isinstance(registered, dict) else {}
    pool = set(registered)
    if bank_hint and by_bank:
        # The hint is usually an SMS sender header ("AD-ICICIT-S") or a bank name, so the BANK NAME
        # is what appears inside the hint, not the other way round. Both directions are tried because
        # the caller may pass either. No match simply leaves the full pool in place.
        hint = re.sub(r"[^a-z0-9]", "", bank_hint.lower())
        same = set()
        for l4, b in by_bank.items():
            if not b:
                continue
            bn = re.sub(r"[^a-z0-9]", "", b.lower())
            if bn and (bn in hint or hint in bn):
                same.add(l4)
        if same:
            pool = same
    for m in _MASKED_NUM.finditer(text):
        digits = _trailing_digits(m.group(1))
        if not digits:
            continue
        if len(digits) >= 4:
            tail = digits[-4:]
            if tail in pool:
                return tail
            if tail in set(registered):      # bank hint was wrong; the full number still wins
                return tail
            continue
        # a partial mask: match by suffix, but only when exactly one account can be meant
        hits = {r for r in pool if r.endswith(digits)}
        if len(hits) == 1:
            return hits.pop()
    return None


def registered_accounts(db: Session, household_id: int | None = None) -> dict[str, str]:
    """{last4: bank} for the household's active accounts — the pool a masked number is matched into."""
    from models.account import Account
    q = db.query(Account.last4, Account.bank).filter(Account.active.is_(True))
    if household_id is not None:
        q = q.filter(Account.household_id == household_id)
    return {l4: (b or "") for l4, b in q.all() if l4}


def repair_unattributed(db: Session, household_id: int, *, dry_run: bool = False) -> dict:
    """Re-attribute rows stuck on a placeholder last4 by reading their OWN stored text.

    The masked-number resolver ran only at ingest, against the raw SMS. Any row whose account was not
    resolved then stayed unattributed forever, even when the answer was sitting in its own
    description: "Transfer to A/c xxxxxxxxxx3456" names the account outright. Rows also predate the
    resolver entirely, and re-ingesting is not an option because the source message may be gone.

    So the same resolver runs over stored text, on every sync, and the ledger self-heals. The `bank`
    column is used as the disambiguation hint (an "ICICI Bank" row can only mean an ICICI account),
    with the merchant text as a fallback hint since it often names the bank too.
    """
    accts = registered_accounts(db, household_id)
    if not accts:
        return {"scanned": 0, "fixed": 0, "by_account": {}}

    rows = (db.query(Transaction)
            .filter(Transaction.household_id == household_id)
            # LEGACY_ARTEFACT_LAST4 is data this household's ledger still carries, not a card number.
            .filter(Transaction.card_last4.in_(
                list(PLACEHOLDER_LAST4 - {None}) + sorted(LEGACY_ARTEFACT_LAST4)))
            .all())
    excluded = excluded_last4(db, household_id)
    by_account: dict[str, int] = {}
    fixed = 0
    for t in rows:
        # source_text first: it is what the bank actually wrote, before parsing lost detail
        blob = " ".join(x for x in (getattr(t, "source_text", None), t.description, t.merchant) if x)
        if not blob:
            continue
        # A row naming an EXCLUDED account must not be adopted into the household — it should have
        # been skipped at ingest and re-attributing it now would launder it in.
        if is_excluded_text(blob, excluded):
            continue
        hint = t.bank if (t.bank or "").lower() not in ("sms", "manual", "") else t.merchant
        guess = resolve_last4_from_text(blob, accts, bank_hint=hint)
        if not guess:
            # The text names no account. Fall back to what the household would know from habit:
            # a recurring merchant comes off the same card every time.
            guess = _infer_from_history(db, t, household_id, accts)
        if not guess or guess == t.card_last4:
            continue
        if not dry_run:
            resolved = resolve_card(db, guess, household_id=household_id)
            if resolved:
                t.bank, t.card_type = resolved
            t.card_last4 = guess
        by_account[guess] = by_account.get(guess, 0) + 1
        fixed += 1
    if not dry_run:
        db.commit()
    return {"scanned": len(rows), "fixed": fixed, "by_account": by_account, "dry_run": dry_run}


def _infer_from_history(db: Session, t, household_id: int, accts: dict[str, str]) -> str | None:
    """What a person would just know: this merchant always comes off the same card.

    A recurring charge debits one account. An EMI of an exact, non-round amount comes off the same
    account every month; a food-delivery merchant may only ever be on one card. When the bank is known, the
    search is restricted to that bank's accounts first, so a merchant used on two different banks'
    cards is not collapsed to the wrong one.

    Requires a clear majority and more than one prior, because a single earlier row is a coincidence,
    not a habit.
    """
    from collections import Counter

    same_bank = None
    if t.bank and t.bank.lower() not in ("sms", "manual", ""):
        bn = re.sub(r"[^a-z0-9]", "", t.bank.lower())
        same_bank = {l4 for l4, b in accts.items()
                     if b and re.sub(r"[^a-z0-9]", "", b.lower()) == bn}

    # An exact recurring AMOUNT is the strongest signal there is, and stronger than the merchant
    # string: the same EMI arrives labelled "HDFC Bank Loan" one month and "EMI 100200300" the next,
    # so matching on merchant text misses it entirely.
    # Restricted to non-round amounts, because Rs 500 or Rs 1,000 recurring proves nothing.
    is_distinctive = round(t.amount) % 100 != 0
    candidates = []
    if is_distinctive:
        candidates.append((db.query(Transaction.card_last4).filter(
            Transaction.household_id == household_id, Transaction.amount == t.amount,
            Transaction.id != t.id), 2, 0.8))
    candidates.append((db.query(Transaction.card_last4).filter(
        Transaction.household_id == household_id, Transaction.merchant == t.merchant,
        Transaction.id != t.id), 2, 0.7))

    for q, min_n, min_share in candidates:
        hits = [l4 for (l4,) in q.all() if l4 in accts]
        if same_bank:
            narrowed = [l4 for l4 in hits if l4 in same_bank]
            # Only narrow when the bank actually has history; otherwise the bank field is telling us
            # about the ALERT's sender, not where the money left from (an ICICI loan debiting an HDFC
            # account is normal), and filtering on it would throw away the real answer.
            if narrowed:
                hits = narrowed
        if len(hits) < min_n:
            continue
        top, n = Counter(hits).most_common(1)[0]
        if n / len(hits) >= min_share:
            return top

    # nothing in history, but the bank has exactly one account — there is only one thing it can be
    if same_bank and len(same_bank) == 1:
        return next(iter(same_bank))
    return None
