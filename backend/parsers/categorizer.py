"""Rule-based transaction classifier.

Returns (category, confidence, txn_type). Order matters — higher-signal rules
(salary, capital, transfers, EMIs, savings) come FIRST so they aren't swallowed
by generic spend keywords. txn_type decides what counts as discretionary SPEND:

    SPEND     genuine discretionary spend (the only thing in "spend by category")
    INCOME    salary + capital inflows (EPF, redemptions, loan disbursals)
    EMI       loan / EMI instalments
    SAVING    SIP / RD / PPF / investments
    TRANSFER  money moved between own accounts
    PAYMENT   credit-card bill payments (and received)
    REFUND    refunds / cashback / reversals / claim payouts
    FEE       bank/card fees, taxes, FX markups, depository charges
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Category -> txn_type for DEBITS comes from the canonical taxonomy (single source of truth).
# Anything mapped to SPEND there (or absent) is discretionary spend.
from services.categories import CATEGORY_TYPE

# (substrings, category, confidence). First hit wins. Keep tokens specific —
# e.g. "emi " (trailing space) so it doesn't match an employer name ending in -emi.
_RULES: list[tuple[list[str], str, str]] = [
    # ── Salary / employers (mostly credits) ──
    # A household's own employers are personal data — they come from the household rules file.
    (["salary"], "Income", "high"),

    # ── Capital inflows (credits) — not spend, not refund ──
    (["provident fund", "epfo", "hdfc securities", "client dscn", "redemption",
      "loan disbursal", "rtgs cr-", "mutual fund redem"],
     "Income", "high"),

    # ── Internal transfers (own accounts) ──
    (["ib funds transfer", "funds transfer", "self transfer",
      "own account", "imps-self"], "Internal Transfer", "high"),

    # ── Loan / EMI ("emi " trailing-space avoids an employer name ending in -emi; instaloan/int nbr) ──
    # A household's own lenders (builder, gold-loan NBFC) come from the household rules file. Note a
    # lender can sit on BOTH sides: its lending arm is a debit (Loan EMI), its disbursal a credit (Income).
    # These tokens only decide a narration that OPENS with "EMI " once no merchant was recognised
    # behind it — see instalment_of(). A card EMI conversion is spend, not debt servicing.
    (["emi ", "emi-", "instaloan", "int nbr", "prin nb", "payment towards loan",
      "loan account",
      "home loan", "bajaj fin", "hdb financial"], "Loan EMI", "high"),

    # ── Savings & investments ──
    (["etmoney", "et money", "groww", "zerodha", "coindcx", "kuvera", "indmoney",
      "rd installment", "rd instal", "rd through", "recurring deposit",
      "mutual fund", "sip ", "ppf", "nps ", "elss", "smallcase"],
     "Savings & Investments", "high"),

    # ── Insurance ──
    (["policy bazaar", "policybazaar", "new india assurance", "lic premium",
      "hdfc life", "icici pru", "star health", "max life", "insurance", "assurance"],
     "Insurance", "high"),

    # ── Refunds / reversals / CC payment ──
    (["refund", "cashback", "reversal", "rebate"], "Refund", "high"),
    (["credit card payment", "card payment", "payment received", "infinity payment",
      "autopay", "bill pay", "billdesk cc",
      # CC bill-pay descriptions where "billpay" has no space — HDFC "IB BILLPAY DR-..." and
      # "BIL/INFT/.../CC BillPay-..." both pay OFF a card; they are NOT utility spend.
      # PayU-AxisBank is a bank bill-payment gateway (paying a card via PayU) — confirmed with the
      # household as CC bill payments, not purchases. Bare "payu" is NOT here (general gateway).
      "ib billpay", "cc billpay", "cc bill", "credit card bill", "payuaxisbank"], "Credit Card Payment", "high"),

    # ── Subscriptions / SaaS / entertainment ──
    (["netflix", "spotify", "amazon prime", "hotstar", "zee5", "youtube", "cursor",
      "openai", "chatgpt", "claude", "anthropic", "cloudflare", "fal.ai",
      "elevenlabs", "github", "aws ", "google cloud", "google workspace", "notion"],
     "Entertainment & Subscriptions", "high"),

    # ── Groceries / quick-commerce ──
    (["swiggy instamart", "instamart", "blinkit", "blink commerce", "bundl", "zepto",
      "bigbasket", "grofer", "dmart", "more supermarket", "mini mart",
      "country delight", "fraazo", "supermart"], "Groceries", "high"),

    # ── Food & dining ──
    (["swiggy", "zomato", "dominos", "mcdonald", "kfc", "burger", "pizza", "cafe",
      "restaurant", "bier", "brews", "barbeque", "eazydiner", "taco", "biryani",
      "sweets", "bakery", "kitchen", "qsr", "kiosk"],
     "Food & Dining", "high"),

    # ── Transport / fuel ──
    (["uber", "ola", "rapido", "irctc", "indigo", "metro", "petrol", "fuel",
      "fastag", "porter", "redbus", "state transport",
      "reliance bp", "hpcl", "iocl", "bpcl"], "Transport", "high"),

    # ── Home & furniture ──
    (["ikea", "furnish", "urban company", "urbanclap", "pepperfry",
      "homecentre", "mr diy"], "Shopping", "high"),

    # ── Shopping / retail ──
    (["amazon", "flipkart", "myntra", "ajio", "nykaa", "meesho", "decathlon",
      "firstcry"],
     "Shopping", "high"),

    # ── Health ──
    (["apollo", "practo", "medplus", "pharmeasy", "hospital", "clinic", "doctor",
      "pharma", "medical", "1mg", "netmeds", "aster"], "Health", "high"),

    # ── Utilities ──
    (["airtel", "/vi/", "vodafone", "jio", "bsnl", "electricity", "water bill",
      "gas bill", "broadband", "bescom", "tata power"], "Bills & Utilities", "high"),

    # ── Travel ──
    (["hotel", "airbnb", "booking.com", "oyo", "goibibo", "makemytrip", "cleartrip"],
     "Travel", "high"),

    # ── Education ──
    (["coursera", "udemy", "unacademy", "byju", "school", "college",
      "university", "tuition"], "Education", "high"),

    (["atm", "cash withdrawal", "cash wdl"], "Personal / Misc", "high"),

    # ── Fees / taxes / charges ──
    (["annual fee", "late fee", "finance charge", "fuel surcharge", "gst on", "igst-",
      "cgst", "sgst", "service charge", "processing fee", "fcy markup", "markup fee",
      "dcc transac", "depository charge", "amc ", "convenience fee", "interest paid"],
     "Fees & Charges", "high"),
]


# ── Household-specific tokens ────────────────────────────────────────────────
# A household's own employers, lenders and neighbourhood shops identify the household, so they are
# data, not code: they live in a gitignored JSON file keyed by category, e.g.
#     {"Income": ["acme corp"], "Loan EMI": ["someBuilderHousing", "somenbfc money"]}
# Each token is appended to the FIRST rule carrying that category, so rule ORDER — which decides
# which category wins — is exactly what it was before the token was moved out of this file.
# Missing or malformed file = the built-in rules alone; ingest never fails because of it.
HOUSEHOLD_RULES_PATH = Path(
    os.environ.get("ARTHA_HOUSEHOLD_RULES")
    or Path(__file__).resolve().parent.parent.parent / "data" / "household_rules.json"
)


def _load_household_tokens(path: Path = HOUSEHOLD_RULES_PATH) -> dict[str, list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(cat): [str(t).lower() for t in toks]
            for cat, toks in data.items() if isinstance(toks, list)}


def _merge_household_tokens(rules: list[tuple[list[str], str, str]],
                            tokens: dict[str, list[str]]) -> None:
    """Append each category's tokens to the first rule with that category. In-place, idempotent."""
    for category, toks in tokens.items():
        for substrings, cat, _conf in rules:
            if cat == category:
                substrings.extend(t for t in toks if t not in substrings)
                break


_merge_household_tokens(_RULES, _load_household_tokens())


# ── Card EMI conversion vs loan repayment ───────────────────────────────────────────────────────
# An issuer rewrites a converted card purchase as "EMI <MERCHANT><CITY>". That leading marker says
# the amount is paid in INSTALMENTS — a fact about HOW it is paid, not WHO is paid. A Google
# Workspace bill split into parts is still a subscription; only a payment to a lender is debt
# servicing. So the marker is stripped and the MERCHANT behind it is what gets classified.
_INSTALMENT_MARKERS = ("emi ", "emi-")


def instalment_of(description: str | None) -> str | None:
    """The purchase behind a card EMI conversion, or None when this is not one."""
    text = (description or "").lstrip()
    low = text.lower()
    for marker in _INSTALMENT_MARKERS:
        if low.startswith(marker):
            return text[len(marker):].strip() or None
    return None


def _match(text: str) -> tuple[str, str] | None:
    low = text.lower()
    for substrings, category, confidence in _RULES:
        if any(s in low for s in substrings):
            return (category, confidence)
    return None


def categorize(description: str) -> tuple[str, str]:
    """(category, confidence) — case-insensitive substring match; fallback Others/low."""
    if not description:
        return ("Personal / Misc", "low")
    # A recognised merchant behind the marker wins; an unrecognised one is left to the rules below,
    # so a bare loan-account narration stays a loan and no unknown payee is guessed into spend.
    purchase = instalment_of(description)
    hit = _match(purchase) if purchase else None
    return hit or _match(description) or ("Personal / Misc", "low")


def classify(description: str, direction: str) -> tuple[str, str, str]:
    """(category, confidence, txn_type). Direction-aware so credits become income/
    refund/transfer — never SPEND."""
    category, confidence = categorize(description)

    if direction == "credit":
        if category in ("Income", "Personal / Misc"):
            txn_type = "INCOME"
        elif category == "Credit Card Payment":
            txn_type = "PAYMENT"
        elif category == "Internal Transfer":
            txn_type = "TRANSFER"
        else:
            txn_type = "REFUND"  # refund / claim payout / cashback on a spend merchant
    else:
        txn_type = CATEGORY_TYPE.get(category, "SPEND")

    return (category, confidence, txn_type)
