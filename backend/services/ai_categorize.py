"""AI transaction categorization — clean merchant name + category from messy bank text.

Uses Claude Haiku (cheap, high-volume). Falls back to None when the AI is unavailable so callers keep
the existing rules-based category. Dedups by description upstream (callers pass DISTINCT descriptions),
so cost stays tiny even across thousands of rows. The LLM picks a category ONLY from the canonical set —
it cannot invent categories, and it never sees or computes amounts.
"""

from __future__ import annotations

import json
import logging

from services import llm
from services.categories import VARIABLE_CATEGORIES, Category

log = logging.getLogger("artha.ai_categorize")

# The AI may choose from the 10 everyday spend buckets PLUS the money-movement categories it most
# often needs — so a person-to-person transfer or a loan payment is NOT forced into a spend bucket
# (the old bug: transfers piled into the misc bucket and inflated spend).
_AI_EXTRA = [Category.INTERNAL_TRANSFER, Category.LOAN_EMI, Category.CC_PAYMENT,
             Category.SAVINGS, Category.RENT, Category.INSURANCE]
_ALLOWED = [c.value for c in VARIABLE_CATEGORIES] + [c.value for c in _AI_EXTRA]

# Every category the prompt names is interpolated from the enum: a rename in the taxonomy rewrites
# the instructions too, instead of teaching the model a label the app would then reject.
_SYSTEM = (
    "You categorise Indian bank / UPI / credit-card transactions. For each, return a clean human "
    "merchant name (Title Case; strip ref numbers, UPI handles like @ybl, and rail noise like "
    "RAZ*/PYU*/UPI/IMPS/NEFT) and the SINGLE best category from the ALLOWED list.\n"
    "RULES — apply in order, first match wins:\n"
    "1. Money to an INDIVIDUAL PERSON (a human's name e.g. 'Alpha Nair', 'B R Menon', or a bare "
    f"'Money Transfer'/'Funds Transfer' with no business) = '{Category.INTERNAL_TRANSFER.value}' — this "
    "is NOT spending. "
    "EXCEPTION: a registered business that merely reads like a name ('Asha Tiffins', 'Ramesh Sweets', "
    "'Ramdev Medical') IS a real merchant → give it the proper spend category below.\n"
    "1b. Narration explicitly about rent (says 'rent', 'house rent', 'landlord') = "
    f"'{Category.RENT.value}' — a "
    "fixed committed cost, overriding the person-transfer rule. (A specific landlord paid by name with no "
    "'rent' text can't be known here — the household teaches that payee via a correction.)\n"
    "2. Loan / EMI repayment (any '... Loan', 'EMI', a lender like Kotak/Bajaj/HDFC/ICICI Loan) = "
    f"'{Category.LOAN_EMI.value}'. The LENDER must be who is being PAID. The bank that issued the "
    "card is not the lender — 'spent on ICICI Bank Card XX7654 at AMAZON PAY' is a purchase at "
    "Amazon, not a loan repayment, and a card purchase at a named merchant is NEVER a loan EMI. "
    "The issuing bank's name appears in every card alert, so matching on it alone hides real "
    "spending: it tagged a card purchase as an EMI, which dropped it out of spend "
    "entirely and made the day look like two transactions instead of three.\n"
    "2b. A narration OPENING with 'EMI ' is usually a CARD EMI CONVERSION — a purchase the issuer "
    "split into instalments. Being paid in instalments says HOW it is paid, not WHO is paid: "
    "categorise it by the MERCHANT after the marker. 'EMI GOOGLE WORKSPACEMUMBAI' is a software "
    "subscription, 'EMI POLICYBAZAAR COMGURGAON' an insurance premium, 'EMI INDIGO AINEGURGAON' a "
    f"flight. It is '{Category.LOAN_EMI.value}' only when what follows is a lender or a loan "
    "account number rather than a merchant ('EMI 455708020 CHQ S4557080200371').\n"
    "3. Credit-card bill payment (CRED, 'card payment', BBPS card, AUTOPAY to a card) = "
    f"'{Category.CC_PAYMENT.value}'.\n"
    "4. Food delivery / restaurant / cafe / bakery / tiffin / sweets (Swiggy, Zomato, ...) = "
    f"'{Category.FOOD_DINING.value}'.\n"
    "5. Quick-commerce / supermarket / kirana (Blinkit, Blink Commerce, Zepto, Instamart, BigBasket, "
    f"DMart, mini mart) = '{Category.GROCERIES.value}'.\n"
    "6. Pharmacy / clinic / hospital / diagnostics / doctor (Apollo, Practo, Qikwell, '* Medical', "
    f"'* Pharmacy') = '{Category.HEALTH.value}'.\n"
    "7. SIP / mutual fund / stocks (Groww, Zerodha, Coin, Indmoney) = "
    f"'{Category.SAVINGS.value}'.\n"
    "8. Insurance premium (LIC, GIC, Lombard, term/health policy) = "
    f"'{Category.INSURANCE.value}'.\n"
    f"9. Otherwise choose the best everyday spend bucket. Use '{Category.PERSONAL.value}' ONLY when "
    "genuinely unclassifiable — never as a lazy default for a person transfer or a known merchant.\n"
    "Output JSON only."
)


def categorize(descriptions: list[str], db=None) -> dict[str, dict] | None:
    """Map each description → {"merchant", "category"}.

    With `db`: check the LEARNED store first (services.learned) and only send UNSEEN merchants to
    AI — then save the AI answers back so they're free next time. Returns None only when there's
    nothing to return at all."""
    if not descriptions:
        return None

    out: dict[str, dict] = {}
    todo = list(descriptions)

    if db is not None:
        from services.learned import classify_text, remember, signature
        todo = []
        for d in descriptions:
            hit = classify_text(db, d)
            if hit and hit.get("category"):
                out[d] = hit
            else:
                todo.append(d)
        db.commit()  # persist hit counters

    if todo and llm.available():
        ai = _ai_categorize(todo)
        if ai:
            for d, info in ai.items():
                out[d] = info
                if db is not None and info.get("category"):
                    from services.learned import remember, signature
                    remember(db, signature(d), info["category"], info.get("merchant"), "ai")
            if db is not None:
                db.commit()
    return out or None


def _ai_categorize(descriptions: list[str]) -> dict[str, dict] | None:
    """The raw AI pass (no memory). Batched; failed batches are skipped."""
    if not llm.available() or not descriptions:
        return None

    out: dict[str, dict] = {}
    BATCH = 25
    for i in range(0, len(descriptions), BATCH):
        chunk = descriptions[i:i + BATCH]
        numbered = "\n".join(f"{j}. {d}" for j, d in enumerate(chunk))
        prompt = (
            f"ALLOWED categories: {_ALLOWED}\n\n"
            f"Transactions (index. raw description):\n{numbered}\n\n"
            'Return a JSON array, one object per index: '
            '[{"i": 0, "merchant": "...", "category": "..."}]. JSON only, no prose.'
        )
        raw = llm.complete(prompt, system=_SYSTEM, model=None, max_tokens=2048)
        if not raw:
            continue
        try:
            s = raw.strip()
            if s.startswith("```"):
                s = s.strip("`").lstrip("json").strip()
            arr = json.loads(s)
            for obj in arr:
                idx = obj.get("i")
                if idx is None or idx >= len(chunk):
                    continue
                cat = obj.get("category")
                out[chunk[idx]] = {
                    "merchant": (obj.get("merchant") or "").strip()[:60] or None,
                    "category": cat if cat in _ALLOWED else None,
                }
        except Exception as e:  # noqa: BLE001
            log.warning("categorize batch %d parse failed: %s", i, e)
            continue
    return out or None
