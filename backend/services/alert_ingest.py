"""Turn bank transaction-alert emails into transactions — the no-upload daily capture.

The LLM reads each alert (varied bank formats) and extracts amount / merchant / date / card / category.
It NEVER computes balances — it only extracts what the email literally states. Non-spends (OTP, balance
info, refunds, credits) are skipped. Dedup is by Gmail message id, so re-syncing is safe. Requires the AI
key; without it this is a no-op (manual + statements still work).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from models.transaction import Transaction
from services import gmail_sync, learned, llm
from services.accounts import resolve_card
from services.ai_categorize import _ALLOWED
from services.categories import CATEGORY_TYPE, Category

log = logging.getLogger("artha.alert_ingest")

_SYSTEM = (
    "You read Indian bank/credit-card transaction-ALERT emails and extract the spend. Use only what the "
    "email states; never compute or guess a balance. is_spend=true ONLY for a debit that ALREADY "
    "HAPPENED (spent/debited/paid). Mark is_spend=false for OTPs, balance/limit info, refunds, credits, "
    "failed/declined txns, anything with no debit amount, AND future/reminder alerts (\"will be "
    "debited\", \"is due\", \"EMI due\", \"due on\", \"scheduled\", a future date)."
)


def _fp(msg_id: str) -> str:
    return hashlib.sha1(f"gmail-alert:{msg_id}".encode()).hexdigest()


def sync_alerts(db: Session, days: int = 14, household_id: int | None = None) -> dict:
    if not llm.available():
        return {"ok": False, "reason": "AI not configured", "added": 0}
    # Work out what is already ingested BEFORE downloading anything. Every message body is a separate
    # HTTP round trip, so filtering afterwards meant paying for mail we then threw away — the bulk of
    # an 18s sync that usually finds nothing new.
    from models.transaction import DeletedFingerprint, EvaluatedSms
    from services.tenancy import resolve_household_id
    household_id = resolve_household_id(db, household_id)   # None must not mean "every household"

    suppressed = {r[0] for r in db.query(DeletedFingerprint.fingerprint)
                  .filter(DeletedFingerprint.household_id == household_id).all()}
    evaluated = {r[0] for r in db.query(EvaluatedSms.fingerprint)
                 .filter(EvaluatedSms.household_id == household_id).all()}
    imported = {r[0] for r in db.query(Transaction.fingerprint)
                .filter(Transaction.household_id == household_id).all() if r[0]}
    seen_fps = suppressed | evaluated | imported

    alerts = gmail_sync.fetch_transaction_alerts(days=days, known_fps=seen_fps, fp=_fp)
    if not alerts:
        return {"ok": True, "scanned": 0, "added": 0, "note": "nothing new (already imported)"}

    from services.accounts import excluded_last4
    _excluded = excluded_last4(db, household_id)

    fresh = [a for a in alerts if _fp(a["id"]) not in seen_fps]
    if not fresh:
        return {"ok": True, "scanned": len(alerts), "added": 0, "note": "all already imported"}

    added = 0
    BATCH = 12
    for i in range(0, len(fresh), BATCH):
        chunk = fresh[i:i + BATCH]
        blocks = "\n\n".join(
            f"[{j}] date:{a['date']}\nfrom:{a['sender']}\nsubject:{a['subject']}\n"
            f"summary:{a.get('snippet','')[:400]}\nbody:{a['body'][:800]}"
            for j, a in enumerate(chunk)
        )
        prompt = (
            f"ALLOWED categories: {_ALLOWED}\n\n"
            f"Alert emails:\n{blocks}\n\n"
            'For each, return JSON: [{"i":0,"is_spend":true,"amount":1200.00,"merchant":"Blinkit",'
            f'"date":"2026-06-22","card_last4":"1234","category":"{Category.GROCERIES.value}"}}]. '
            "amount = the debited amount (number). date = yyyy-mm-dd. JSON only."
        )
        raw = llm.complete(prompt, system=_SYSTEM, max_tokens=2048)
        if not raw:
            continue
        try:
            s = raw.strip()
            if s.startswith("```"):
                s = s.strip("`").lstrip("json").strip()
            data = json.loads(s)
            for obj in data:
                idx = obj.get("i")
                if idx is None or idx >= len(chunk) or not obj.get("is_spend") or not obj.get("amount"):
                    continue
                a = chunk[idx]
                when = (obj.get("date") or "")[:10]
                if not when:
                    try:
                        when = datetime.strptime(a["date"][:25].strip(), "%a, %d %b %Y %H:%M:%S").date().isoformat()
                    except Exception:  # noqa: BLE001
                        when = date.today().isoformat()
                # precise email-received time → Tier-1 dedup (best-effort parse of the Date header)
                try:
                    from email.utils import parsedate_to_datetime
                    occurred = parsedate_to_datetime(a["date"])
                    occurred = occurred.astimezone().replace(tzinfo=None) if occurred and occurred.tzinfo else occurred
                except Exception:  # noqa: BLE001
                    occurred = None
                cat = obj.get("category")
                merchant = obj.get("merchant")
                last4 = (obj.get("card_last4") or "0000")[:4]
                # Same guard on the email path, and for the same reason: the account number lives
                # in the alert's prose, so relying on the extractor alone made the exclusion optional.
                from services.accounts import is_excluded_text
                _blob = f"{a.get('snippet','')} {a.get('body','')}"
                hit = last4 if last4 in _excluded else is_excluded_text(_blob, _excluded)
                if hit:  # employer card / another person's account on a shared mailbox → skip
                    continue
                # Join the real physical card by last-4. The alert parser often misreads the number
                # (account-tail, ref fragment) → if it doesn't map to a card the household actually
                # owns, DON'T invent a phantom "••XXXX" card; book it against the bank account instead.
                resolved = resolve_card(db, last4, household_id=household_id)
                if resolved:
                    bank, ctype = resolved
                else:
                    bank = (a["sender"].split("@")[-1].split(".")[0][:12] or "bank").title()
                    ctype, last4 = "Account", "0000"
                # Apply the household's learned corrections at capture time: once a payee is tagged
                # (e.g. a person you transfer to → Internal Transfer), every future alert for that
                # payee inherits it, instead of coming back as spend.
                hit = learned.classify_text(db, merchant or a["subject"])
                if hit and hit.get("category") in _ALLOWED:
                    cat = hit["category"]
                # "Others" was the fallback here — a name the taxonomy dropped in the 21→18 merge, so
                # every unmatched alert landed on a category no query or picker recognises.
                cat = cat if cat in _ALLOWED else Category.PERSONAL.value
                from services.bank_ref import extract_ref
                ref = extract_ref(f"{a.get('body', '')} {a.get('snippet', '')}")  # UPI RRN / NEFT UTR
                # Per-row SAVEPOINT: the periodic sync and the API on-open sync race to insert the same
                # alert fingerprint; without isolation the loser's UniqueViolation poisoned the session and
                # the final commit rolled back the whole batch → 'added:0'. begin_nested skips just the dup.
                from sqlalchemy.exc import IntegrityError
                try:
                    with db.begin_nested():
                        db.add(Transaction(source_text=(f"{a.get('snippet','')} {a.get('body','')}")[:2000], 
                            fingerprint=_fp(a["id"]), date=when, occurred_at=occurred, bank_ref=ref,
                            description=merchant or a["subject"][:60],
                            merchant=merchant, amount=float(obj["amount"]),
                            direction="debit", category=cat,
                            confidence="high", txn_type=CATEGORY_TYPE.get(cat, "SPEND"),
                            bank=bank, card_type=ctype, card_last4=last4,
                            stmt_month=when[:7], source="gmail",
                        ))
                    added += 1
                except IntegrityError:
                    continue   # a concurrent sync already booked this alert — skip, batch stays healthy
            # Tombstone alerts the model EXPLICITLY returned as non-spend so the next sync stops
            # re-classifying them (same 'new=1, added=0 forever' waste the SMS path had). Shared
            # evaluated_sms table; gmail-alert fingerprints are namespaced so they never collide.
            from models.transaction import EvaluatedSms
            spend_idx = {o.get("i") for o in data if o.get("is_spend") and o.get("amount")}
            returned_idx = {o.get("i") for o in data if isinstance(o.get("i"), int)}
            for j, aa in enumerate(chunk):
                if j in returned_idx and j not in spend_idx:
                    db.merge(EvaluatedSms(household_id=household_id, fingerprint=_fp(aa["id"])))
        except Exception as e:  # noqa: BLE001
            log.warning("alert batch %d failed: %s", i, e)
            continue
    db.commit()
    # Auto-merge high-confidence cross-source duplicates (e.g. this email + its matching SMS) so the
    # total is correct without anyone clicking "merge". Reversible. Only when something new came in.
    from services.dedup import auto_merge
    merged = auto_merge(db, household_id=household_id)["merged_rows"] if added else 0
    return {"ok": True, "scanned": len(alerts), "new_alerts": len(fresh), "added": added,
            "auto_merged": merged}
