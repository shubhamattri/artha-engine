"""Extract the bank's own transaction identifiers from an SMS / email / statement narration.

Two keys, in order of strength:
  • bank_ref  — the reference the bank assigns (UPI RRN, NEFT/IMPS UTR, "Ref 400123456789",
                "Txn No HDFCH1234..."). Same ref = the same real debit anywhere; different ref =
                different debits, never merge. Present on UPI / NEFT / IMPS, absent on card POS.
  • txn_time  — the precise transaction time printed INSIDE the message ("Spent ... On
                2026-07-01:15:51:20"; Axis "01-07-26 11:09:46 IST"). Card POS txns carry no ref but do
                carry this — the strongest same-debit signal for cards (two identical times = one debit
                forwarded twice; different times = different debits).

Both are best-effort — return None when the text doesn't carry one. Pure regex, deterministic, no LLM.
"""

from __future__ import annotations

import re
from datetime import datetime

# A labelled reference number: "Ref 400123456789", "UPI Ref no. 4127...", "RRN: 4127...". 9-18 digits so
# it can't be a 4-digit card tail or a 10-digit phone (which never carry a Ref/RRN label anyway).
_RE_REF = re.compile(r"(?:UPI[\s:/-]*)?(?:Ref(?:erence)?|RRN)\.?\s*(?:no\.?)?[:#\s/-]*(\d{9,18})\b", re.I)
# HDFC writes its posting id as "(Ref# ST111122223333444455501)" — a LETTER prefix then a long digit
# run. The digits-only pattern above returns None for it, which is why two ₹99 fees on one day, whose
# refs differed only in the final digit (…501 vs …502), had no way to be told apart and were counted
# as a duplicate. Require >=8 digits inside the token so a word after "Ref" can never match.
_RE_REF_ALNUM = re.compile(r"(?:Ref(?:erence)?|RRN)\.?\s*(?:no\.?)?[:#\s/-]*([A-Z]{1,4}\d{8,22})\b", re.I)
# NEFT / IMPS transaction number: "Txn No HDFCH12345678901" (bank code + digits, alphanumeric).
_RE_TXN_NO = re.compile(r"\bTxn\s*No\.?\s*([A-Z]{2,6}[A-Z0-9]{8,22})\b", re.I)

# UNLABELLED / POSITIONAL references — the majority of the real data (statement narrations + SMS) carry
# the reference by POSITION, not behind a "Ref" label. Each pattern is anchored so the captured group can
# ONLY be a real reference — never a VPA phone number, a card tail, or an amount. This is the difference
# between bank_ref being NULL everywhere (fuzzy guessing) and deterministic same-ref/different-ref dedup.
_RE_POSITIONAL: list[re.Pattern] = [
    # IMPS-500123456789-PAYEE NAME-…   (the token right after IMPS- is the RRN, unique per transfer)
    re.compile(r"\bIMPS[-/\s]+(\d{9,16})\b", re.I),
    # NOTE: "IB FUNDS TRANSFER DR/CR-<n>" is DELIBERATELY NOT parsed — <n> is the beneficiary ACCOUNT
    # number, constant across every transfer to that payee, so using it as a ref collapses all recurring
    # transfers into one (verified: the same ₹5,000 transfer to one payee ×4 across Jan–May shared it).
    # Account ≠ reference.
    re.compile(r"\bNEFT[-/\s]+([A-Z0-9]{9,22})\b", re.I),
    re.compile(r"\bRTGS[-/\s]+([A-Z0-9]{9,22})\b", re.I),
    # UPI RRN — the 12-digit segment ANCHORED before the trailing -UPI marker
    # (…-UTIB0000001-400111222333-UPI). Anchoring to -UPI stops it grabbing a VPA phone number.
    re.compile(r"-(\d{12})-UPI\b", re.I),
    # PayU / aggregator id:  A1B2C3D4E5F6G7H8I9/PAYUAXISBANK
    re.compile(r"\b([A-Z0-9]{12,})/PAYU", re.I),
    # Card-network posting id: a line STARTING with the 10-12 digit posting ref then the merchant name
    # ("40012345678 Example Retail Ltd …"). Anchored to string start so it can't be a mid-string number.
    re.compile(r"^\s*(\d{10,12})\s+[A-Za-z]", re.I),
]
_JUNK = {"", "0", "00", "000", "0000"}

# HDFC card / UPI: "On 2026-07-01:15:51:20"  (date and time joined by a colon).
_RE_TIME_ISO = re.compile(r"On\s+(\d{4}-\d{2}-\d{2}):(\d{2}:\d{2}:\d{2})")
# Axis card: "01-07-26 11:09:46 IST".
_RE_TIME_DMY = re.compile(r"\b(\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\b")


def extract_ref(text: str | None) -> str | None:
    """The bank reference (UPI RRN / NEFT-IMPS UTR / posting id), upper-cased, or None.

    Labelled refs first (most reliable), then unlabelled positional refs. Returns None when the text
    carries no UNAMBIGUOUS reference — that row then goes to the review queue, never a wrong auto-merge.
    """
    if not text:
        return None
    m = _RE_REF.search(text)
    if m:
        return m.group(1)
    m = _RE_REF_ALNUM.search(text)
    if m:
        return m.group(1).upper()
    m = _RE_TXN_NO.search(text)
    if m:
        return m.group(1).upper()
    for pat in _RE_POSITIONAL:
        m = pat.search(text)
        if m:
            ref = (m.group(1) or "").strip().upper()
            if ref and ref not in _JUNK and ref.strip("0"):
                return ref
    return None


def backfill(db, household_id: int | None = None, *, dry_run: bool = False) -> dict:
    """Populate bank_ref on every row where it's NULL and a reference is extractable from the narration.

    Idempotent + safe: only fills NULLs, never overwrites an existing ref. Returns counts + a sample so
    the impact can be eyeballed before commit. This is what turns the historical ledger (statements never
    called extract_ref) into deterministically de-dupable data. Scope to one household or all (None)."""
    from datetime import date as _date

    from models.transaction import Transaction

    q = db.query(Transaction).filter(Transaction.bank_ref.is_(None))
    if household_id is not None:
        q = q.filter(Transaction.household_id == household_id)
    rows = q.all()

    # Pass 1 — collect candidate refs.
    candidates: dict[int, str] = {}          # row.id -> extracted ref
    dates_by_ref: dict[str, set] = {}        # ref -> set of transaction dates it appears on
    for r in rows:
        # description is the SHORT merchant name for sms/gmail rows; their reference lives in
        # source_text. Reading only description meant those rows re-scanned "Delhivery Limited"
        # every sync and could never be filled, which is how this reported scanned 2398, filled 0.
        ref = extract_ref(r.description) or extract_ref(r.source_text)
        if ref:
            candidates[r.id] = ref
            dates_by_ref.setdefault(ref, set()).add(r.date[:10])

    # Pass 2 — POISON GUARD. A genuine per-transaction reference belongs to ONE debit, so all rows
    # carrying it fall within a few days (the same debit re-captured across channels). A ref that spans a
    # wider window is a RECURRING identifier (account/standing-instruction number) that leaked through a
    # pattern — using it would merge unrelated transactions. Drop every such ref before writing.
    def _span_days(ds: set) -> int:
        pts = sorted(_date.fromisoformat(x) for x in ds)
        return (pts[-1] - pts[0]).days
    poison = {ref for ref, ds in dates_by_ref.items() if _span_days(ds) > 3}

    filled, sample, dropped = 0, [], 0
    by_id = {r.id: r for r in rows}
    for rid, ref in candidates.items():
        if ref in poison:
            dropped += 1
            continue
        filled += 1
        r = by_id[rid]
        if len(sample) < 12:
            sample.append({"id": rid, "ref": ref, "amount": r.amount, "desc": (r.description or "")[:64]})
        if not dry_run:
            r.bank_ref = ref
    if not dry_run:
        db.commit()
    return {"scanned": len(rows), "filled": filled, "dropped_recurring": dropped,
            "poison_refs": len(poison), "dry_run": dry_run, "sample": sample}


def extract_txn_time(text: str | None) -> datetime | None:
    """The precise transaction time stamped in the message body (naive local), or None."""
    if not text:
        return None
    m = _RE_TIME_ISO.search(text)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    m = _RE_TIME_DMY.search(text)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d-%m-%y %H:%M:%S")
        except ValueError:
            return None
    return None
