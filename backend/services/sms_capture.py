"""Capture bank transaction SMS from the Mac's Messages database (the all-Apple-household solution).

iPhones forward SMS to this Mac (Settings → Messages → Text Message Forwarding); macOS stores them in
~/Library/Messages/chat.db. SMS is RBI-mandated on EVERY transaction, so this gives 100% coverage that
email can't (ICICI/HDFC don't email per-txn). We read ONLY bank-sender / transaction-keyword messages —
never personal chats — and the LLM extracts amount/merchant/date/card. Dedup by message guid.

Requirements (one-time): (1) Text Message Forwarding ON from each iPhone → this Mac; (2) the reading
process needs macOS Full Disk Access. Read-only DB access; we never write to chat.db.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from services.categories import Category

log = logging.getLogger("artha.sms_capture")

_APPLE_EPOCH = 978307200  # 2001-01-01 in unix seconds


def _chat_dbs() -> list[str]:
    from config import settings
    return [p for p in settings.chat_dbs if os.path.exists(p)]


def _decode_attr(blob: bytes | None) -> str | None:
    """Extract plain text from a macOS Messages `attributedBody` (streamtyped NSAttributedString).
    Newer macOS stores the body here instead of the `text` column. Heuristic but reliable for SMS."""
    if not blob:
        return None
    try:
        if b"NSString" not in blob:
            return None
        b = blob.split(b"NSString", 1)[1][5:]  # skip class-ref bytes after 'NSString'
        if not b:
            return None
        if b[0] == 0x81:                         # 0x81 → next 2 bytes = little-endian length
            ln = int.from_bytes(b[1:3], "little"); body = b[3:3 + ln]
        else:                                    # else 1-byte length
            ln = b[0]; body = b[1:1 + ln]
        text = body.decode("utf-8", "ignore").strip()
        return text or None
    except Exception:  # noqa: BLE001
        return None

# Indian bank SMS sender headers look like VM-HDFCBK, AX-ICICIB, JD-AxisBk, AD-SBIINB, etc.
# Bank of Baroda uses BOBCRD (credit card), BOBTXN (account debits/credits) and BOBSMS (general).
# NOT "BARB": that substring matches Barbeque Nation's marketing sender (qp-barbeq), not the bank.
_BANK_LIKE = ("HDFC", "ICICI", "AXIS", "SBIN", "SBI", "KOTAK", "AXISBK", "BK",
              "BOBCRD", "BOBTXN", "BOBSMS")

# Senders that only ever carry one-time passwords. Excluded deliberately: an OTP is a live credential,
# it is never a transaction, and this pipeline hands message bodies to an LLM. BOBOTP is in chat.db
# and WOULD have been swept in by a broader "BOB" match.
_NEVER = ("OTP",)
_KEYWORDS = ("debited", "spent", "credited", "txn", "purchase", "withdrawn", "debit", "trf")


def available() -> bool:
    return bool(_chat_dbs())


def _senders_for(db=None, household_id: int | None = None) -> tuple[str, ...]:
    """Sender fragments to match: the built-in banks PLUS whatever this household registered.

    Without the registered half, adding a card in the UI creates a row nobody reads — the messages
    are dropped by this module's SQL long before anything downstream could see them. The built-ins
    stay so a household that has registered nothing still captures the common banks.
    """
    extra: list[str] = []
    if db is not None:
        try:
            from models.account import Account
            q = db.query(Account).filter(Account.active.is_(True))
            if household_id is not None:
                q = q.filter(Account.household_id == household_id)
            for a in q.all():
                extra += [t.strip().upper() for t in (a.sms_senders or "").split(",") if t.strip()]
        except Exception:  # noqa: BLE001 — pre-041 install, or no DB
            pass
    # Dedup while keeping it deterministic, and drop anything that would widen into the OTP block.
    seen, out = set(), []
    for tok in (*_BANK_LIKE, *extra):
        t = tok.strip().upper()
        if t and t not in seen and not any(n in t for n in _NEVER):
            seen.add(t)
            out.append(t)
    return tuple(out)


def read_bank_sms(since_days: int = 30, limit: int = 300, db=None,
                  household_id: int | None = None) -> list[dict]:
    """[{guid, sender, text, when}] for bank/transaction SMS only, across every configured chat.db
    (both spouses). Empty if unreadable (no FDA) or not present. Never selects personal messages.

    Pass `db` so senders registered by the household are included alongside the built-in banks.
    """
    dbs = _chat_dbs()
    if not dbs:
        return []
    cutoff_ns = int((datetime.now(timezone.utc).timestamp() - since_days * 86400 - _APPLE_EPOCH) * 1e9)
    # Filter by bank-like SENDER only (privacy: never decode personal chats). Body may be in `text`
    # or `attributedBody` (newer macOS) — fetch both and decode.
    sender_like = " OR ".join(f"upper(h.id) LIKE '%{b}%'" for b in _senders_for(db, household_id))
    sender_block = " AND ".join(f"upper(h.id) NOT LIKE '%{b}%'" for b in _NEVER)
    sql = (
        "SELECT m.guid, h.id, m.text, m.attributedBody, m.date "
        "FROM message m JOIN handle h ON m.handle_id=h.ROWID "
        f"WHERE m.is_from_me=0 AND m.date > {cutoff_ns} AND ({sender_like}) AND ({sender_block}) "
        f"ORDER BY m.date DESC LIMIT {limit}"
    )
    out: list[dict] = []
    for path in dbs:
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            rows = con.execute(sql).fetchall()
            con.close()
        except Exception as e:  # noqa: BLE001 — usually macOS Full Disk Access not granted
            log.warning("chat.db read failed for %s (Full Disk Access?): %s", path, e)
            continue
        for guid, sender, text, attr, dt in rows:
            body = text or _decode_attr(attr)
            if not body:
                continue
            # KEEP the precise time (was being reduced to a day) — it's the strongest dedup signal.
            # LOCAL, not UTC. The date a purchase belongs to is the date it happened where it
            # happened. Taking the UTC date filed every transaction between 00:00 and 05:30 IST one
            # day early: a Swiggy order at 00:33 on the 13th was stored as the 12th, and the bank's
            # own SMS said 13-Aug. With the cycle boundary on the 13th that pushed it into the
            # PREVIOUS bill, so it was both invisible on the day it happened and counted in the
            # wrong cycle.
            when_dt = datetime.fromtimestamp(dt / 1e9 + _APPLE_EPOCH, timezone.utc).astimezone()
            when_dt = when_dt.replace(tzinfo=None)
            out.append({"guid": guid, "sender": sender, "text": body,
                        "when": when_dt.date().isoformat(), "when_ts": when_dt})
    return out


def sync_sms(db: Session, since_days: int = 30, household_id: int | None = None) -> dict:
    """Read bank SMS → LLM-extract → insert transactions (source='sms', dedup by guid)."""
    import hashlib
    import json

    from models.transaction import Transaction
    from services import llm
    from services.ai_categorize import _ALLOWED

    if not available():
        return {"ok": False, "reason": "chat.db not found — Messages not signed in / no SMS forwarding", "added": 0}
    if not llm.available():
        return {"ok": False, "reason": "AI not configured", "added": 0}

    from services.tenancy import resolve_household_id
    household_id = resolve_household_id(db, household_id)   # None must not mean "every household"

    msgs = read_bank_sms(since_days, db=db, household_id=household_id)
    if not msgs:
        return {"ok": True, "scanned": 0, "added": 0,
                "note": "no bank SMS in chat.db yet (enable Text Message Forwarding + Full Disk Access)"}

    def fp(guid: str) -> str:
        return hashlib.sha1(f"sms:{guid}".encode()).hexdigest()

    from models.transaction import DeletedFingerprint, EvaluatedSms
    suppressed = {r[0] for r in db.query(DeletedFingerprint.fingerprint)
                  .filter(DeletedFingerprint.household_id == household_id).all()}
    evaluated = {r[0] for r in db.query(EvaluatedSms.fingerprint)
                 .filter(EvaluatedSms.household_id == household_id).all()}
    skip = suppressed | evaluated

    from services.accounts import excluded_last4
    _excluded = excluded_last4(db, household_id)

    fresh = [m for m in msgs
             if fp(m["guid"]) not in skip
             and not db.query(Transaction.id).filter(Transaction.fingerprint == fp(m["guid"])).first()]
    added = 0
    for i in range(0, len(fresh), 15):
        chunk = fresh[i:i + 15]
        blocks = "\n\n".join(f"[{j}] from:{m['sender']} date:{m['when']}\n{m['text'][:400]}" for j, m in enumerate(chunk))
        prompt = (
            f"ALLOWED categories: {_ALLOWED}\n\nBank SMS:\n{blocks}\n\n"
            'For each, JSON: [{"i":0,"is_spend":true,"amount":543.0,"merchant":"Blinkit","date":"2026-06-22",'
            f'"card_last4":"1234","category":"{Category.GROCERIES.value}"}}]. '
            "is_spend=true ONLY for a debit that ALREADY HAPPENED (spent/debited/paid/withdrawn). "
            "is_spend=false for credits/OTP/balance/promo AND for FUTURE or reminder alerts — anything "
            'saying "will be debited", "is due", "EMI due", "due on", "scheduled", "payment due", or a '
            "future date. amount=debited number. JSON only."
        )
        raw = llm.complete(prompt, max_tokens=2048,
                           system="Extract spends from Indian bank transaction SMS. Use only stated values; never invent.")
        if not raw:
            continue
        try:
            # Parse the FIRST JSON array out of the reply, tolerating markdown fences and any trailing
            # prose — json.loads() is strict and throws "Extra data" on a single stray char, which was
            # silently dropping whole batches of real spend SMS.
            s = raw.strip()
            start = s.find("[")
            if start == -1:
                continue
            data, _ = json.JSONDecoder().raw_decode(s[start:])
            for obj in data:
                idx = obj.get("i")
                if idx is None or idx >= len(chunk) or not obj.get("is_spend") or not obj.get("amount"):
                    continue
                m = chunk[idx]
                cat = obj.get("category")
                merchant = obj.get("merchant")
                last4 = (obj.get("card_last4") or "0000")[:4]
                # Check the RAW TEXT too, not only what the extractor produced. HDFC InstaAlerts
                # name the account in prose ("debited from your account ending 5566"), which the
                # extractor can miss — the row then lands as "0000" and the exclusion compares
                # "0000" against the list, letting a non-household account straight through.
                from services.accounts import is_excluded_text
                hit = last4 if last4 in _excluded else is_excluded_text(m["text"], _excluded)
                if hit:  # employer card / another person's account on a shared mailbox → skip
                    log.info("skipped alert for excluded account ending %s", hit)
                    continue
                from services.accounts import resolve_card  # local import avoids cycle
                from services.categories import CATEGORY_TYPE
                # Only trust the last-4 if it maps to a card the household owns; otherwise the SMS
                # parser misread it → book against the bank account, don't invent a phantom card.
                resolved = resolve_card(db, last4, household_id=household_id)
                if not resolved:
                    # The extractor could not place it. Before giving up to the phantom bucket, read
                    # the masked number out of the message itself: banks write "Acc XX456" (three
                    # digits) and taking the last four CHARACTERS produced "0456", a card that does
                    # not exist. The sender's own name disambiguates a partial match.
                    from services.accounts import registered_accounts, resolve_last4_from_text
                    accts = registered_accounts(db, household_id)
                    guess = resolve_last4_from_text(m["text"], accts, bank_hint=m.get("sender"))
                    if guess:
                        last4 = guess
                        resolved = resolve_card(db, last4, household_id=household_id)
                if resolved:
                    bank, ctype = resolved
                else:
                    bank, ctype, last4 = "SMS", "Account", "0000"
                # The deterministic classifier (merchant_rules → learned → keyword) is AUTHORITATIVE for
                # objective categories — especially CC bill-payments and internal transfers
                # ("CC BillPay", "IMPS-self", "INFT …CC") that the LLM mislabels as a Personal spend and
                # so inflate the numbers by lakhs. Run it on the FULL SMS text (which carries those
                # markers); trust it when confident, else fall back to the LLM's category.
                from services.classifier import classify as classify_txn
                det_cat, det_conf, _ = classify_txn(db, m["text"], "debit")
                if det_conf == "high":
                    cat = det_cat
                elif cat not in _ALLOWED:
                    # "Others" was the fallback here — a name the taxonomy dropped in the 21→18 merge,
                    # so every unmatched SMS landed on a category no query or picker recognises.
                    cat = Category.PERSONAL.value
                # A premium debit must land on the insurance category or it never reaches the Insurance
                # domain + commitment reconciler. Only fires on a HIGH-confidence keyword hit.
                from parsers.categorizer import categorize as _kw_categorize
                _det_cat, _det_conf = _kw_categorize(merchant or m["text"])
                if _det_conf == "high" and _det_cat == Category.INSURANCE.value:
                    cat = Category.INSURANCE.value
                # The bank's own identifiers, straight from the SMS text: a reference (UPI RRN / NEFT
                # UTR) and/or the precise transaction time (card 'Spent...On <ts>'). The text time beats
                # the SMS receipt time (m["when_ts"]) — it's the actual debit moment the bank stamped.
                from services.bank_ref import extract_ref, extract_txn_time
                ref = extract_ref(m["text"])
                ts = extract_txn_time(m["text"]) or m.get("when_ts")
                # A debit can't post AFTER the bank's own alert SMS. The LLM sometimes lifts a due-date
                # or a mis-read date from the text and lands in the future — clamp any extracted date to
                # the SMS receipt date so we never book a future-dated transaction.
                recv = m["when"][:10]
                txn_date = (obj.get("date") or m["when"])[:10]
                if txn_date > recv:
                    txn_date = recv
                # Per-row SAVEPOINT so one duplicate can't sink the whole batch. The periodic launchd
                # sync and the API's on-open sync can run concurrently: both compute the same "fresh"
                # set and race to insert the same fingerprint. Without isolation the loser's UniqueViolation
                # poisoned the session and the FINAL commit rolled back EVERYTHING → 'new:32, added:0' every
                # run + a 500. begin_nested() rolls back just the losing row and keeps the batch healthy.
                from sqlalchemy.exc import IntegrityError
                try:
                    with db.begin_nested():
                        db.add(Transaction(source_text=m["text"][:2000], 
                            fingerprint=fp(m["guid"]), date=txn_date,
                            occurred_at=ts, bank_ref=ref,   # → ref/timestamp dedup (RECONCILIATION-SPEC.md)
                            description=merchant or m["text"][:60], merchant=merchant,
                            amount=float(obj["amount"]), direction="debit",
                            category=cat, confidence="high", txn_type=CATEGORY_TYPE.get(cat, "SPEND"),
                            bank=bank, card_type=ctype, card_last4=last4,
                            stmt_month=txn_date[:7], source="sms",
                        ))
                    added += 1
                except IntegrityError:
                    continue   # a concurrent sync already booked this exact SMS — skip, batch stays healthy
            # Tombstone only the messages the model EXPLICITLY returned as non-spend, so the next sync
            # skips re-classifying them. A message the LLM OMITTED (e.g. a truncated reply) is left
            # untombstoned and retried next run — we never silently bury a spend the model didn't judge.
            # Only runs after a successful parse; merge() is an idempotent, race-safe upsert.
            spend_idx = {o.get("i") for o in data if o.get("is_spend") and o.get("amount")}
            returned_idx = {o.get("i") for o in data if isinstance(o.get("i"), int)}
            for j, mm in enumerate(chunk):
                if j in returned_idx and j not in spend_idx:
                    db.merge(EvaluatedSms(household_id=household_id, fingerprint=fp(mm["guid"])))
        except Exception as e:  # noqa: BLE001
            log.warning("sms batch %d failed: %s", i, e)
            continue
    db.commit()
    # Auto-merge high-confidence cross-source duplicates so the headline number is never wrong and
    # the user never has to click "merge". Reversible (services.dedup.undo). Only on fresh inserts.
    from services.dedup import auto_merge
    merged = auto_merge(db, household_id=household_id)["merged_rows"] if added else 0
    return {"ok": True, "scanned": len(msgs), "new": len(fresh), "added": added, "auto_merged": merged}


def scan_all_senders(since_days: int = 90, limit: int = 4000) -> list[dict]:
    """Every SMS sender that looks like a business/bank header, with a redacted sample.

    Deliberately BROADER than read_bank_sms: that function only returns senders we already know, which
    is precisely why an unrecognised card stays invisible. This one reports what is arriving so the UI
    can ask "is this yours?". Personal chats are excluded structurally — a sender containing lowercase
    letters or a leading + is a phone number or an iMessage address, not a bank shortcode.
    """
    dbs = _chat_dbs()
    if not dbs:
        return []
    cutoff_ns = int((datetime.now(timezone.utc).timestamp() - since_days * 86400 - _APPLE_EPOCH) * 1e9)
    agg: dict[str, dict] = {}
    for path in dbs:
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT h.id, m.text, m.attributedBody, m.date FROM message m "
                "JOIN handle h ON m.handle_id=h.ROWID "
                f"WHERE m.is_from_me=0 AND m.date > {cutoff_ns} "
                f"ORDER BY m.date DESC LIMIT {limit}"
            ).fetchall()
            con.close()
        except Exception as e:  # noqa: BLE001
            log.warning("sender scan failed for %s: %s", path, e)
            continue
        for sender, text, attr, dt in rows:
            s = (sender or "").strip()
            # Bank/business shortcodes are alphanumeric with dashes and no lowercase-only phone shape.
            if not s or s.startswith("+") or "@" in s or s.replace("+", "").isdigit():
                continue
            when = datetime.fromtimestamp(dt / 1e9 + _APPLE_EPOCH, timezone.utc).replace(tzinfo=None)
            e = agg.setdefault(s, {"sender": s, "count": 0, "last_seen": None, "sample": "",
                                   "transactional": False})
            e["count"] += 1
            # Does this sender ever report an actual debit/credit? A shortcode that only sends
            # delivery updates, OTP-adjacent notices or marketing is not an account, and offering it
            # as one turns a useful prompt into 200 rows of noise nobody reads.
            raw = (text or _decode_attr(attr) or "").lower()
            if any(k in raw for k in _KEYWORDS):
                e["transactional"] = True
            if e["last_seen"] is None or when.isoformat() > e["last_seen"]:
                e["last_seen"] = when.isoformat()
                body = text or _decode_attr(attr) or ""
                # Never surface anything that looks like a live credential in a UI listing.
                body = re.sub(r"\b\d{4,8}\b", "•••", body)
                e["sample"] = body[:110]
    return list(agg.values())
