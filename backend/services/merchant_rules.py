"""Canonical merchant → category map — the consistency layer in front of the classifier.

The problem it fixes: the same real merchant arrives under many bank strings ("Bundl Technologies",
"Bundl Techn", "SWIGGY", "Swiggy Ltd"…). The learned store keys on the literal signature, so those become
DIFFERENT keys and each got an independent (inconsistent) AI guess — Swiggy ended up split across Food,
Entertainment, Bills and Shopping. Here we resolve known brands to ONE (merchant, category) deterministically,
BEFORE any AI, so a given brand is always the same category everywhere (Plan, Analytics, Spending).

CONSERVATIVE ON PURPOSE: only brands that are unambiguously ONE category live here. Genuinely multi-category
payers (Amazon, Amazon Pay, Google Pay, PhonePe, Paytm wallet, Apple bill) are intentionally absent — they
stay per-transaction for the AI / the user to decide.
"""

from __future__ import annotations

from services.categories import Category

FOOD = Category.FOOD_DINING.value
GROC = Category.GROCERIES.value
TRANSPORT = Category.TRANSPORT.value
SHOP = Category.SHOPPING.value
BILLS = Category.BILLS.value
HEALTH = Category.HEALTH.value
ENT = Category.ENTERTAINMENT.value
TRAVEL = Category.TRAVEL.value
PERSONAL = Category.PERSONAL.value

# (canonical merchant, category, [substrings — lowercased, matched anywhere in the bank text])
_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("Swiggy", FOOD, ("swiggy", "bundl")),                         # Bundl Technologies == Swiggy
    # Eternal Ltd == Zomato. Tokens are kept SHORT because banks truncate the narration: the ledger
    # holds "ETERNAL LIM", which "eternal limited" never matched, so those rows fell through to an AI
    # guess and landed in Personal / Misc. "eternal li" + "eternal lt" survive truncation at 10-11
    # chars while still being specific enough not to catch an unrelated "Eternal" business.
    ("Zomato", FOOD, ("zomato", "eternal li", "eternal lt")),
    ("Rebel Foods", FOOD, ("rebel foods", "faasos", "behrouz", "ovenstory", "oven story")),
    ("Domino's", FOOD, ("domino",)),
    ("McDonald's", FOOD, ("mcdonald", "mc donald")),
    ("KFC", FOOD, ("kfc",)), ("Pizza Hut", FOOD, ("pizza hut",)), ("Burger King", FOOD, ("burger king",)),
    ("Starbucks", FOOD, ("starbucks",)), ("Chaayos", FOOD, ("chaayos",)), ("Third Wave", FOOD, ("third wave",)),
    ("FreshMenu", FOOD, ("freshmenu",)), ("Wow! Momo", FOOD, ("wow momo", "wow! momo")),
    ("Barbeque Nation", FOOD, ("barbeque nation", "barbeque-nation")),

    ("Blinkit", GROC, ("blinkit", "blink comm", "grofers")),   # 'blink comm' also catches truncated "Blink Comme"
    ("Zepto", GROC, ("zepto", "geddit", "kiranakart")),
    ("Swiggy Instamart", GROC, ("instamart",)),
    ("BigBasket", GROC, ("bigbasket", "big basket", "innovative retail")),
    ("JioMart", GROC, ("jiomart", "jio mart")),
    ("DMart", GROC, ("dmart", "avenue supermart", "d-mart")),
    ("Licious", GROC, ("licious",)), ("FreshToHome", GROC, ("freshtohome", "fresh to home")),
    ("Country Delight", GROC, ("country delight",)), ("Milkbasket", GROC, ("milkbasket", "milk basket")),

    ("Uber", TRANSPORT, ("uber",)), ("Ola", TRANSPORT, ("olacabs", "ola cabs", "ani technologies")),
    ("Rapido", TRANSPORT, ("rapido",)), ("Namma Yatri", TRANSPORT, ("namma yatri",)),
    ("Indian Oil", TRANSPORT, ("indian oil", "indianoil", "iocl")),
    ("Bharat Petroleum", TRANSPORT, ("bharat petroleum", "bpcl")),
    ("Hindustan Petroleum", TRANSPORT, ("hindustan petroleum", "hpcl")),
    ("Shell", TRANSPORT, ("shell ",)), ("FASTag", TRANSPORT, ("fastag", "fast tag")),

    ("Netflix", ENT, ("netflix",)), ("Spotify", ENT, ("spotify",)),
    ("Disney+ Hotstar", ENT, ("hotstar", "disney")), ("SonyLIV", ENT, ("sonyliv", "sony liv")),
    ("ZEE5", ENT, ("zee5",)), ("JioCinema", ENT, ("jiocinema", "jio cinema")),
    ("YouTube Premium", ENT, ("youtube premium", "google youtube")),
    ("Prime Video", ENT, ("prime video",)), ("Audible", ENT, ("audible",)),
    ("BookMyShow", ENT, ("bookmyshow", "book my show")), ("PVR", ENT, ("pvr cinemas", "pvr inox", "pvr ltd")),

    ("MakeMyTrip", TRAVEL, ("makemytrip", "make my trip")), ("Goibibo", TRAVEL, ("goibibo",)),
    ("Cleartrip", TRAVEL, ("cleartrip",)), ("Ixigo", TRAVEL, ("ixigo",)),
    ("IRCTC", TRAVEL, ("irctc",)), ("IndiGo", TRAVEL, ("indigo", "interglobe aviation")),
    ("Air India", TRAVEL, ("air india",)), ("Vistara", TRAVEL, ("vistara",)),
    ("OYO", TRAVEL, ("oyo",)), ("redBus", TRAVEL, ("redbus", "red bus")),

    ("Myntra", SHOP, ("myntra",)), ("AJIO", SHOP, ("ajio",)), ("Nykaa", SHOP, ("nykaa",)),
    ("Meesho", SHOP, ("meesho",)), ("Flipkart", SHOP, ("flipkart",)),
    ("Tata CLiQ", SHOP, ("tata cliq", "tatacliq")), ("FirstCry", SHOP, ("firstcry", "first cry")),
    ("Decathlon", SHOP, ("decathlon",)), ("IKEA", SHOP, ("ikea",)),
    ("Croma", SHOP, ("croma", "infiniti retail")), ("Reliance Digital", SHOP, ("reliance digital",)),

    ("PharmEasy", HEALTH, ("pharmeasy", "pharm easy")), ("Tata 1mg", HEALTH, ("1mg", "tata 1mg")),
    ("Netmeds", HEALTH, ("netmeds",)), ("Apollo Pharmacy", HEALTH, ("apollo pharmacy", "apollo hospital")),
    ("Practo", HEALTH, ("practo",)), ("cult.fit", HEALTH, ("cult.fit", "cultfit", "curefit", "cure.fit")),
    ("MedPlus", HEALTH, ("medplus", "med plus")),

    ("Urban Company", PERSONAL, ("urban company", "urbanclap")),
]

# precompute (substring -> (merchant, category)), MOST-SPECIFIC (longest substring) FIRST so a specific
# brand beats a broad one: "Swiggy Instamart" must match 'instamart' (Groceries) before 'swiggy' (Food).
_INDEX: list[tuple[str, str, str]] = sorted(
    ((sub, merchant, cat) for merchant, cat, subs in _RULES for sub in subs),
    key=lambda x: -len(x[0]),
)


# ---------------------------------------------------------------------------------------------
# GENERIC TIER — matched only when no specific brand does.
#
# Named brands can't cover a category whose merchants are overwhelmingly LOCAL. Petrol is the worst
# case: a single ledger can hold a dozen distinct strings for buying fuel ("lakeview fuels", "green valley
# fuel", "sunrise fuel park", "metro fuel junction", "s and b fuels", "s b fuels", "r k fuel
# park"…), and every unfamiliar pump used to fall through to an independent AI guess — so the same
# correction had to be made again for each new pump. A keyword IS reliable evidence here: a merchant
# with "fuel"/"petrol" in its name is buying fuel, whoever owns the pump.
#
# (label, category, must contain ANY of these, but NONE of these)
_GENERIC: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = [
    ("Fuel", TRANSPORT,
     ("fuel", "petrol", "petroleum", "filling station", "service station", "gas station", "petro "),
     # card fees and money coming BACK are not a fuel purchase
     ("surcharge", "gst", " fee", "reversal", "cashback", "rebate", "refund")),
    ("Toll / Parking", TRANSPORT, ("fastag", "toll plaza", "parking"), ()),
]


def resolve(text: str | None) -> dict | None:
    """{'merchant','category'} for a known brand or a generic pattern, or None. Deterministic, no AI.
    Specific brands win over generic patterns, so "Indian Oil" stays "Indian Oil" and only an
    unrecognised pump falls through to the generic "Fuel"."""
    if not text:
        return None
    t = text.lower()
    for sub, merchant, cat in _INDEX:
        if sub in t:
            return {"merchant": merchant, "category": cat}
    for label, cat, include, exclude in _GENERIC:
        if any(k in t for k in include) and not any(k in t for k in exclude):
            return {"merchant": label, "category": cat}
    return None


def apply(db, household_id: int, *, dry_run: bool = False) -> dict:
    """Recategorise SPEND debits to their canonical brand category so a brand is consistent across all
    months. The brand map is objective for real brands (Swiggy IS food delivery), so it overrides a
    wrong AI guess — but it NEVER overrides the user.

    THE USER OUTRANKS THE MAP. Previously this swept every row and then wrote
    `rule.category, rule.kind = cat, "rule"` straight onto the CategoryRule, bypassing
    learned.remember()'s trust ladder (user=2 > ai=rule=1). So a category the user had explicitly
    fixed was silently reverted on the next sweep, and their learned rule was downgraded from "user"
    to "rule" — which is why a correction had to be made over and over. Rows and signatures the user
    has explicitly taught are now left alone, matching the guard the AI resweep already had.

    dry_run computes the diff without writing. Returns {changed, skipped_user, by_merchant}."""
    from models.category_rule import CategoryRule
    from models.transaction import Transaction
    from services import learned
    from services.audit import log_event

    # Signatures the user has explicitly taught — these are ground truth, the brand map is not.
    user_sigs = {r.signature for r in
                 db.query(CategoryRule.signature)
                 .filter(CategoryRule.household_id == household_id, CategoryRule.kind == "user").all()}

    rows = (db.query(Transaction)
            .filter(Transaction.household_id == household_id, Transaction.direction == "debit",
                    Transaction.txn_type == "SPEND", Transaction.archived.is_(False)).all())
    changed = 0
    skipped_user = 0
    by_merchant: dict[str, dict] = {}
    fix_sigs: dict[str, str] = {}      # signature -> canonical category (to repair the learned store)
    for t in rows:
        text = t.merchant or t.description or ""
        canon = resolve(text)
        if not canon:
            continue
        sig = learned.signature(text)
        if sig in user_sigs:           # the user has ruled on this merchant — do not touch it
            if t.category != canon["category"]:
                skipped_user += 1
            continue
        fix_sigs[sig] = canon["category"]
        if t.category == canon["category"]:
            continue
        m = by_merchant.setdefault(canon["merchant"], {"to": canon["category"], "from": {}, "n": 0})
        m["from"][t.category] = m["from"].get(t.category, 0) + 1
        m["n"] += 1
        changed += 1
        if not dry_run:
            log_event(db, household_id=t.household_id, txn_id=t.id, action="recategorize",
                      field="category", old=t.category, new=canon["category"], actor="merchant-rule")
            t.category, t.confidence = canon["category"], "high"
    if not dry_run:
        # repair learned rules that disagree with the canonical brand category — but only AI guesses.
        # Route through remember() so the trust ladder is enforced in ONE place instead of two.
        for sig, cat in fix_sigs.items():
            r = db.query(CategoryRule).filter(CategoryRule.household_id == household_id,
                                              CategoryRule.signature == sig).first()
            if r and r.category != cat and r.kind != "user":
                learned.remember(db, sig, cat, r.merchant, "rule", household_id=household_id)
        db.commit()
    return {"changed": changed, "skipped_user": skipped_user,
            "by_merchant": by_merchant, "dry_run": dry_run}
