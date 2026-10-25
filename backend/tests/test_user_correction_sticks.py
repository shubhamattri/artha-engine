"""A category the user has explicitly set must survive every automated sweep.

Regression: merchant_rules.apply() swept ALL rows and wrote `rule.kind = "rule"` straight onto the
CategoryRule, bypassing learned.remember()'s trust ladder (user > ai/rule). The user's correction was
reverted on the next sweep AND their rule was downgraded — so the same fix had to be made repeatedly.
"""

from models.category_rule import CategoryRule
from models.tenant import Household
from models.transaction import Transaction
from services import learned, merchant_rules


def _txn(hid, merchant, category, i=0):
    return Transaction(household_id=hid, fingerprint=f"f-{merchant}-{i}", date="2026-07-15",
                       description=merchant, merchant=merchant, source="statement", amount=500.0,
                       direction="debit", category=category, txn_type="SPEND", bank="HDFC",
                       card_type="credit", card_last4="1111", stmt_month="2026-07")


def test_user_correction_survives_the_brand_sweep(db_session):
    h = Household(name="U"); db_session.add(h); db_session.commit()
    # Swiggy is Food & Dining in the brand map, but THIS household books it as Groceries on purpose.
    t = _txn(h.id, "Swiggy", "Groceries"); t.confidence = "high"
    db_session.add(t); db_session.commit()
    sig = learned.signature("Swiggy")
    learned.remember(db_session, sig, "Groceries", "Swiggy", "user", household_id=h.id)
    db_session.commit()

    out = merchant_rules.apply(db_session, h.id)
    db_session.refresh(t)

    assert t.category == "Groceries"                    # the sweep did NOT revert the user
    assert out["skipped_user"] >= 1                     # and it says so, rather than silently skipping
    rule = db_session.query(CategoryRule).filter(CategoryRule.household_id == h.id,
                                                 CategoryRule.signature == sig).first()
    assert rule.kind == "user" and rule.category == "Groceries"   # rule not downgraded to "rule"


def test_sweep_still_fixes_ai_mistakes(db_session):
    """The guard must not neuter the sweep: an AI-guessed category is still corrected."""
    h = Household(name="A"); db_session.add(h); db_session.commit()
    t = _txn(h.id, "Swiggy", "Shopping", i=2)           # wrong, and only an AI guess
    db_session.add(t); db_session.commit()
    learned.remember(db_session, learned.signature("Swiggy"), "Shopping", "Swiggy", "ai",
                     household_id=h.id)
    db_session.commit()

    merchant_rules.apply(db_session, h.id)
    db_session.refresh(t)
    assert t.category == "Food & Dining"                # brand map wins over an AI guess
    rule = db_session.query(CategoryRule).filter(CategoryRule.household_id == h.id).first()
    assert rule.category == "Food & Dining"


def test_repeated_sweeps_are_stable(db_session):
    """Running the sweep twice must not flip a user's category back and forth."""
    h = Household(name="S"); db_session.add(h); db_session.commit()
    t = _txn(h.id, "Swiggy", "Groceries", i=3); t.confidence = "high"
    db_session.add(t); db_session.commit()
    learned.remember(db_session, learned.signature("Swiggy"), "Groceries", "Swiggy", "user",
                     household_id=h.id)
    db_session.commit()
    for _ in range(3):
        merchant_rules.apply(db_session, h.id)
        db_session.refresh(t)
        assert t.category == "Groceries"


def test_any_local_petrol_pump_is_transport(db_session):
    """Fuel merchants are overwhelmingly LOCAL — a named-brand map can never cover them. Every
    unfamiliar pump used to fall through to an independent AI guess, so the same correction had to be
    repeated for each new pump name."""
    from services.merchant_rules import resolve
    for name in ("lakeview fuels", "green valley fuel", "s and b fuels", "s b fuels",
                 "sunrise fuel park", "metro fuel junction", "r k fuel park",
                 "SHREE PETROLEUM", "HP FILLING STATION", "any random service station"):
        r = resolve(name)
        assert r and r["category"] == "Transport", f"{name} did not resolve to Transport"
    # a SPECIFIC brand still beats the generic pattern
    assert resolve("indian oil petrol pump")["merchant"] == "Indian Oil"
    # card fees and money coming back are NOT a fuel purchase
    for name in ("Fuel Surcharge", "GST On Fuel Surcharge", "Fuel Surcharge Reversal",
                 "Fuel Cashback Rebates"):
        assert resolve(name) is None, f"{name} should not be treated as a fuel purchase"


def test_ingest_precedence_user_then_brandmap_then_ai(db_session):
    """On import: a user rule beats the brand map; the brand map beats an AI rule; a brand-new local
    pump still resolves via the generic pattern."""
    from services.learned import classify_text, remember, signature
    h = Household(name="P"); db_session.add(h); db_session.commit()

    # 1. user rule outranks the brand map
    remember(db_session, signature("Swiggy"), "Groceries", "Swiggy", "user", household_id=h.id)
    db_session.commit()
    assert classify_text(db_session, "Swiggy", h.id)["category"] == "Groceries"

    # 2. brand map outranks an AI rule
    remember(db_session, signature("Blinkit"), "Shopping", "Blinkit", "ai", household_id=h.id)
    db_session.commit()
    assert classify_text(db_session, "Blinkit", h.id)["category"] == "Groceries"

    # 3. a pump never seen before still lands in Transport with no correction needed
    assert classify_text(db_session, "SAMPLE FUELS BENGALURU", h.id)["category"] == "Transport"
    assert classify_text(db_session, "Totally Unknown Shop", h.id) is None


def test_projection_range_always_brackets_the_estimate(db_session):
    """low <= projected <= high, always. A point estimate outside its own range reads as broken —
    and it happened: the ceiling was the flat-rate figure, which the surprise allowance can exceed."""
    from services.projection import project
    class R:
        def __init__(s, a): s.amount, s.category = a, "X"
    for elapsed, total in ((1, 31), (5, 31), (21, 31), (30, 31), (28, 28)):
        for amounts in ([100.0], [8000.0], [8000.0, 100.0, 250.0], [50.0] * 20, []):
            for rate in (0.0, 50.0, 5000.0):
                p = project([R(a) for a in amounts], elapsed, total, typical=300.0, lump_rate=rate)
                lo, mid, hi = p["projected_low_inr"], p["projected_inr"], p["projected_high_inr"]
                assert lo <= mid <= hi, f"{lo} <= {mid} <= {hi} failed ({amounts}, d{elapsed}/{total}, rate {rate})"
                assert mid >= round(sum(amounts), 2) - 0.01      # never predicts less than already spent


def test_delivery_brands_survive_bank_truncation():
    """Banks truncate the narration, so brand tokens must be short enough to still match.

    "ETERNAL LIM" is what the ledger actually holds for Zomato's parent company; the rule listed
    "eternal limited", never matched, and those rows fell through to an AI guess (Personal / Misc).
    """
    from services.merchant_rules import resolve
    food = {"ETERNAL LIM", "ETERNAL LIMITED", "Eternal Ltd", "ZOMATO LIMI", "ZOMATO LIMITED",
            "Swiggy Limi", "SWIGGY PVT LTD", "Bundl Techn", "BUNDL TECHN", "RAZ*Swiggy",
            "Swiggy Dineout", "Swiggy Marketplace", "SWIGGY FOOD"}
    groc = {"GROFERS IND", "Blink Comme", "BLINK COMMERCE", "BLINKIT ECOM", "Blinkit Foods",
            "INSTAMART", "Instamart Grocery", "RSP*INSTAMART", "Swiggy Instamart"}
    for m in food:
        r = resolve(m)
        assert r and r["category"] == "Food & Dining", f"{m!r} -> {r}"
    for m in groc:
        r = resolve(m)
        assert r and r["category"] == "Groceries", f"{m!r} -> {r}"
    # Instamart is ordered THROUGH Swiggy but is groceries, so the specific token must win.
    assert resolve("Swiggy Instamart")["category"] == "Groceries"
    # ...and "eternal" must stay specific enough not to swallow an unrelated business.
    assert resolve("Eternal Salon") is None
    assert resolve("Eternal Hospital") is None
