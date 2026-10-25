"""Shared statement-ingest pipeline — used by both the manual upload endpoint and the
Gmail auto-pull. ONE place that turns raw statement bytes into deduped transactions:

    bytes → temp → unlock(if encrypted) → detect bank/card → parse → categorise → dedup-insert

Returns a structured result dict (never raises for a 'bad statement' — callers decide how to
surface it), so a batch sync can ingest many files and report per-file outcomes.
"""

from __future__ import annotations

import logging
import os
import tempfile

from config import settings
from models.transaction import Statement, Transaction
from parsers import (
    parse_account_excel,
    parse_axis_pdf,
    parse_hdfc_csv,
    parse_hdfc_pdf,
    parse_icici_pdf,
    parse_kotak_pdf,
)
from parsers.base import compute_fingerprint
from parsers.categorizer import classify
from parsers.detect import detect_statement
from parsers.icici_xlsx import parse_icici_xlsx
from parsers.pdf_unlock import find_password
from services.bank_ref import extract_ref
from sqlalchemy import func
from sqlalchemy.orm import Session

# bank (lowercased) → { extension → parser }
_CARD_PARSERS = {
    "hdfc": {".csv": parse_hdfc_csv, ".pdf": parse_hdfc_pdf},
    "icici": {".pdf": parse_icici_pdf, ".xlsx": parse_icici_xlsx, ".xls": parse_icici_xlsx},
    "kotak": {".pdf": parse_kotak_pdf},
    "axis": {".pdf": parse_axis_pdf},
}
_EXCEL_EXTS = {".xls", ".xlsx"}
_ALL_SUPPORTED_EXTS = {".csv", ".pdf", ".xls", ".xlsx"}


def supported() -> dict:
    banks = [{"bank": b.upper(), "statement": "Credit Card", "accepts": sorted(x)}
             for b, x in _CARD_PARSERS.items()]
    banks.append({"bank": "Any", "statement": "Bank Account", "accepts": sorted(_EXCEL_EXTS)})
    return {"accepted_file_types": sorted(_ALL_SUPPORTED_EXTS), "banks": banks}


def select_parser(bank_key: str, ext: str):
    """(parser, error_message). A bank's OWN parser for this extension wins (e.g. ICICI credit-card
    .xlsx → parse_icici_xlsx); otherwise Excel → generic account parser."""
    by_ext = _CARD_PARSERS.get(bank_key, {})
    if ext in by_ext:
        return by_ext[ext], None
    if ext in _EXCEL_EXTS:
        return parse_account_excel, None
    if bank_key not in _CARD_PARSERS:
        # A bank we have no PDF parser for is NOT a dead end: the household can still register the
        # card and have every alert captured over SMS, and an Excel/CSV export imports today. Saying
        # only "unsupported" made people think the card could not be tracked at all, which is false.
        sup = ", ".join(sorted(_CARD_PARSERS))
        return None, (
            f"We can't read {bank_key.upper() or 'this bank'}'s statement format yet "
            f"(we have readers for {sup}). Two things that DO work: export the statement as "
            f"Excel or CSV and upload that instead, or add the card under Settings → Accounts "
            f"& cards, which captures every transaction alert automatically."
        )
    by_ext = _CARD_PARSERS[bank_key]
    parser = by_ext.get(ext)
    if parser is not None:
        return parser, None
    wanted = " or ".join(sorted(by_ext))
    if ext not in _ALL_SUPPORTED_EXTS:
        return None, (f"We can't read '{ext or 'this file type'}' files. Artha accepts PDF, CSV "
                      f"and Excel statements. For {bank_key.upper()} please upload a {wanted} file.")
    return None, (f"For {bank_key.upper()} credit-card statements we accept a {wanted} file, "
                  f"but you uploaded '{ext}'. Please upload the {wanted} export.")


def _is_known_account(db: Session, household_id: int, last4: str) -> bool:
    """Does this household actually hold an account ending in these digits?"""
    from models.account import Account
    return db.query(Account.id).filter(Account.household_id == household_id,
                                       Account.last4 == last4).first() is not None


def _resolve_card_type(db: Session, bank: str, last4: str, detected_type: str) -> str:
    """Reuse an existing card's label for a known (bank, last4) so re-uploads don't fork."""
    if last4 and bank:
        prior = (db.query(Transaction.card_type)
                 .filter(Transaction.card_last4 == last4)
                 .filter(Transaction.bank.ilike(bank))
                 .group_by(Transaction.card_type)
                 .order_by(func.count().desc()).first())
        if prior and prior[0]:
            return prior[0]
    return detected_type


# An "unbilled" / "current" export is the bank's mid-cycle view of a period that has NOT closed.
# Its rows are real, but the billed statement will list the same transactions again in its own format
# ("BDSKAMAZONPAYIN" becomes "40012345678 AMAZON PAY IN GROCERY"), and both imported as authoritative
# statement rows: supersede skips them because it only looks at sms/gmail, and dedup refuses to
# collapse statement rows because N statement lines mean N real debits. So both copies lived and the
# spend was counted twice -- Rs 60,000 on one card.
#
# Marking them provisional lets the existing supersede do exactly what it already does for SMS: when
# the billed statement lands, match on amount+date and archive the superseded copy. Nothing new to
# reconcile, and the rows stay recoverable because archived is not deleted.
_UNBILLED_MARKERS = ("current", "unbilled", "outstanding")


def is_unbilled_statement(filename: str) -> bool:
    """True when this file is a mid-cycle snapshot rather than a closed statement."""
    stem = (filename or "").rsplit("/", 1)[-1].lower()
    return any(m in stem for m in _UNBILLED_MARKERS)


def _supersede_provisional(db: Session, card_last4: str | None, parsed: list) -> int:
    """Match provisional SMS/Gmail rows to the statement's own rows (amount+date), transfer the
    cleaner name/category onto the statement row, and archive only the MATCHED provisional rows.
    Unmatched provisional rows stay active (the statement doesn't carry them). Returns count merged."""
    from datetime import date as _date

    from services.categories import CATEGORY_TYPE

    dates = [t.date for t in parsed if t.date]
    if not dates or not card_last4:
        return 0
    p_start, p_end = min(dates), max(dates)

    def d(s):
        try:
            return _date.fromisoformat(s[:10])
        except Exception:  # noqa: BLE001
            return None

    provisional = (db.query(Transaction)
                   .filter(Transaction.card_last4 == card_last4,
                           Transaction.source.in_(["sms", "gmail", "statement_unbilled"]),
                           Transaction.archived.is_(False),
                           Transaction.date >= p_start, Transaction.date <= p_end).all())
    stmt_rows = (db.query(Transaction)
                 .filter(Transaction.card_last4 == card_last4, Transaction.source == "statement",
                         Transaction.date >= p_start, Transaction.date <= p_end).all())

    merged = 0
    for p in provisional:
        pd = d(p.date)
        tol = max(2.0, 0.01 * (p.amount or 0))
        best = None
        for s in stmt_rows:
            if s.direction != p.direction or abs((s.amount or 0) - (p.amount or 0)) > tol:
                continue
            sd = d(s.date)
            if pd and sd and abs((pd - sd).days) > 3:
                continue
            best = s
            break
        if best is None:
            continue  # real txn the statement doesn't have → keep the provisional active
        # carry the cleaner provisional name/category onto the statement truth (unless user-set there)
        if best.confidence != "high":
            if p.merchant:
                best.merchant = p.merchant
            if p.category:
                best.category = p.category
                if best.direction == "debit":
                    best.txn_type = CATEGORY_TYPE.get(p.category, best.txn_type)
                if p.confidence == "high":
                    best.confidence = "high"
        p.archived = True
        merged += 1
    db.commit()
    return merged


logger = logging.getLogger(__name__)


def _cycle_spend(db: Session, household_id: int) -> float:
    """The household's net spend for the CURRENT cycle — the number on their screen."""
    try:
        from api.budget import compute_budget
        return float(compute_budget(db=db, household_id=household_id).get("net_spent_inr") or 0.0)
    except Exception:  # noqa: BLE001 — a guard that cannot measure must not block the import
        logger.exception("could not read cycle spend; ingest guard disabled for this file")
        return float("nan")


def _assert_spend_moved_sanely(db: Session, household_id: int, before: float,
                               parsed: list, new_count: int, filename: str) -> None:
    """Roll back and raise if the cycle total moved by more than this file's own rows can explain."""
    import math

    if math.isnan(before):
        return
    after = _cycle_spend(db, household_id)
    if math.isnan(after):
        return
    moved = after - before
    # The most this file can honestly add: every row it parsed. Compared against what was PARSED, not
    # what was inserted, so a dedup miss (inserted a duplicate) still passes while a double-count of
    # the same file does not.
    ceiling = sum(abs(getattr(t, "amount", 0.0) or 0.0) for t in parsed) + 1.0
    if moved > ceiling:
        db.rollback()
        from services.ingest_result import IngestGuardTripped
        raise IngestGuardTripped(
            f"{filename}: cycle spend moved {moved:,.0f} but this file's rows total at most "
            f"{ceiling:,.0f} ({new_count} inserted). Rolled back.")


def ingest_statement(db: Session, filename: str, contents: bytes,
                     bank: str | None = None, card_type: str | None = None,
                     card_last4: str | None = None, household_id: int = 1,
                     hint_passwords: list[str] | None = None,
                     dry_run: bool = False, guard: bool = True,
                     require_known_account: bool = False) -> dict:
    """Ingest one statement file. Returns a result dict; on failure returns {'ok': False, 'error': ...}.
    household_id stamps every created row (defaults to the single household; upload passes the real one).

    dry_run: parse, dedup and report what WOULD be written, then roll back. Nothing touches the
        household's data. Anything that writes transactions must be previewable, because an import
        that reported "0 new" while inserting 43 rows was committed on the strength of its own
        summary.
    guard: after committing, check the cycle's net spend moved by no more than the rows added can
        explain. If it did, roll back and raise IngestGuardTripped. A row count is a proxy; the
        number a person actually sees is the spend total, so that is what gets verified.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    tmp_path = None
    detected = None
    pdf_password = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        if ext == ".pdf":
            # Passwords lifted from the covering email are tried FIRST. Banks state them in the
            # body ("Password abcd1234", or a rule like first-4-of-name + DDMM), so the email that
            # carries the statement also carries the key. Configured passwords remain the fallback.
            candidates = list(hint_passwords or []) + list(settings.pdf_passwords or [])
            found = find_password(tmp_path, candidates, last4=card_last4,
                                  db=db, household_id=household_id)
            if found is None:
                return {"ok": False, "filename": filename,
                        "error": "Password-protected PDF; no configured password opened it "
                                 "(set STATEMENT_PDF_PASSWORDS in backend/.env)."}
            pdf_password = found

        if not (bank and card_type and card_last4):
            detected = detect_statement(tmp_path, ext, filename or "", pdf_password)
            bank = bank or (detected.bank or "")
            card_type = card_type or detected.card_type
            card_last4 = card_last4 or (detected.card_last4 or "")
            card_type = _resolve_card_type(db, bank, card_last4, card_type)

        # REFUSE a statement for an account this household does not have. The card parsers assume a
        # CARD layout; a savings-account statement has a running-balance column they do not expect,
        # so the columns shift by one and the BALANCE is read as the transaction amount. That is not
        # a parse failure that announces itself -- it produces confident, plausible rows. An ICICI
        # savings statement for XX1234 came through the ICICI card sender and booked Rs 25,000 of
        # spend that never happened, against real spend of about Rs 1,700.
        # Only enforced for AUTOMATED imports. A person uploading a file has decided it belongs
        # here, and refusing would break adding a card you have not registered yet. The mailbox has
        # made no such decision -- it hands over whatever the sender happened to send.
        if require_known_account and card_last4 and not _is_known_account(db, household_id, card_last4):
            return {"ok": False, "filename": filename,
                    "error": f"No account ending {card_last4} in this household — refusing to parse "
                             f"it as a card statement. Add the account first if this is yours."}

        parser, error = select_parser((bank or "").strip().lower(), ext)
        if parser is None:
            return {"ok": False, "filename": filename, "error": error}

        try:
            parsed = (parser(tmp_path, card_type, card_last4, pdf_password)
                      if ext == ".pdf" else parser(tmp_path, card_type, card_last4)) or []
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "filename": filename,
                    "error": f"Couldn't read the file ({type(e).__name__})."}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    if not parsed:
        return {"ok": False, "filename": filename,
                "error": "No transactions found (empty, scanned, or unsupported layout)."}

    stmt_month = parsed[0].stmt_month or ""
    statement = None
    if stmt_month:
        # Keyed by FILE as well as month. Matching on (bank, card_type, month) alone meant two
        # different files covering the same month shared one record: the mid-cycle export and the
        # billed statement both wrote into it, so their rows became indistinguishable by origin.
        # That is what made the duplicate cleanup unsafe -- keying on statement_id would have
        # re-marked the billed rows as provisional and let the next import archive the real ones.
        statement = (db.query(Statement).filter(
            Statement.household_id == household_id,
            Statement.bank == parsed[0].bank, Statement.card_type == card_type,
            Statement.stmt_month == stmt_month,
            Statement.filename == (filename or "upload")).first())
    if statement is None:
        statement = Statement(household_id=household_id,
                              filename=filename or "upload", bank=parsed[0].bank,
                              card_type=card_type, card_last4=card_last4 or "",
                              stmt_month=stmt_month or "0000-00", txn_count=0)
        db.add(statement)
        db.flush()

    from models.transaction import DeletedFingerprint
    # Scoped: a tombstone belongs to the household that deleted the row. Unscoped, one household
    # deleting a transaction would suppress every other household's identical one forever.
    suppressed = {r[0] for r in db.query(DeletedFingerprint.fingerprint)
                  .filter(DeletedFingerprint.household_id == household_id).all()}

    # Provisional when the file is a mid-cycle snapshot, so the billed statement supersedes it later
    # instead of sitting alongside it and doubling the spend.
    row_source = "statement_unbilled" if is_unbilled_statement(filename) else "statement"

    new_count = 0
    seen: dict[str, int] = {}
    for t in parsed:
        if t.card_last4 in settings.excluded_card_last4:  # corporate / non-household card → skip
            continue
        base_fp = compute_fingerprint(t)
        seq = seen.get(base_fp, 0)
        seen[base_fp] = seq + 1
        fp = compute_fingerprint(t, seq=seq)
        if fp in suppressed or db.query(Transaction.id).filter(Transaction.fingerprint == fp).first():
            continue
        cat, conf, ttype = classify(t.description, t.direction)
        db.add(Transaction(household_id=household_id, statement_id=statement.id, fingerprint=fp,
                           date=t.date, description=t.description, amount=t.amount, direction=t.direction,
                           category=cat, confidence=conf, txn_type=ttype, bank=t.bank,
                           card_type=card_type, card_last4=card_last4 or "", stmt_month=t.stmt_month,
                           source=row_source,
                           # The statement narration carries the bank's own posting id. Without it
                           # every statement row was ref-less, so two files covering one period had
                           # nothing to match on and dedup could not tell a repeat from a re-import.
                           bank_ref=extract_ref(t.description)))
        new_count += 1

    statement.txn_count = (db.query(func.count(Transaction.id))
                           .filter(Transaction.statement_id == statement.id).scalar() or 0)

    if dry_run:
        db.rollback()
        return {"ok": True, "filename": filename, "dry_run": True,
                "transactions_parsed": len(parsed), "transactions_new": 0,
                "would_insert": new_count, "auto_merged": 0, "superseded_provisional": 0,
                "bank": parsed[0].bank, "card_type": card_type, "card_last4": card_last4,
                "stmt_month": stmt_month, "statement_id": None,
                # The period the statement actually covers, so a reconcile can ask the reverse
                # question too: what does Artha hold in this window that the bank never billed?
                # would_insert alone only finds gaps in one direction.
                "period_start": min(t.date for t in parsed),
                "period_end": max(t.date for t in parsed),
                "sample": [{"date": t.date, "amount": t.amount, "description": t.description[:60]}
                           for t in parsed[:5]]}

    spend_before = _cycle_spend(db, household_id) if guard else None
    db.commit()

    # STATEMENT-SUPERSEDE: the bank statement is the source of truth for this card over its period.
    # For each PROVISIONAL (SMS/Gmail) capture in that window, find its matching statement row by
    # amount+date (NOT merchant name — "Grofers"≡"Blink Commerce"). If matched: carry the cleaner
    # provisional name/category onto the statement row (statements have gibberish merchant codes like
    # "YBEBLITTLEELLYC"), then archive the provisional. If NOT matched: KEEP it — it's a real txn the
    # statement doesn't carry (don't lose it).
    superseded = _supersede_provisional(db, card_last4, parsed)

    # Auto-merge any remaining high-confidence cross-source duplicates so totals are correct with no
    # manual "merge" step. Reversible (services.dedup.undo). Only when this upload added rows.
    from services.dedup import auto_merge
    auto_merged = auto_merge(db, household_id=household_id)["merged_rows"] if new_count else 0

    # The row count is a proxy. What a person sees is the cycle total, so verify THAT moved by no
    # more than the rows added can explain. A duplicate that slipped past dedup shows up here as
    # spend rising with no matching new row, which is exactly what went unnoticed before.
    if guard and spend_before is not None:
        _assert_spend_moved_sanely(db, household_id, spend_before, parsed, new_count, filename)

    return {
        "ok": True, "filename": filename, "statement_id": statement.id,
        "transactions_parsed": len(parsed), "transactions_new": new_count,
        "superseded_provisional": superseded, "auto_merged": auto_merged,
        "bank": parsed[0].bank, "card_type": card_type, "card_last4": card_last4,
        "stmt_month": stmt_month,
        "auto_detected": detected is not None,
        "detect_confidence": detected.confidence if detected else None,
    }
