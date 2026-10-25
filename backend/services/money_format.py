"""The household's currency symbol, for anything that renders money into text.

A household that set currency=EUR must not read its own numbers with a rupee sign in front of them,
so every display string resolves its symbol from here instead of hardcoding one.
"""

from __future__ import annotations

from config import settings

# Households pick an ISO code; anything without a well-known glyph renders as "CODE " (e.g. "AED 1,200"),
# which is unambiguous and never wrong.
_SYMBOLS = {
    "INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥",
    "AUD": "A$", "CAD": "C$", "NZD": "NZ$", "SGD": "S$", "HKD": "HK$",
    "ZAR": "R", "BRL": "R$", "MYR": "RM", "THB": "฿", "PHP": "₱", "KRW": "₩",
    "VND": "₫", "TRY": "₺", "RUB": "₽", "ILS": "₪", "NGN": "₦", "BDT": "৳",
    "LKR": "Rs ", "NPR": "Rs ", "PKR": "Rs ",
}

DEFAULT_SYMBOL = _SYMBOLS.get(settings.currency.upper(), settings.currency.upper() + " ")


def symbol_for_code(code: str | None) -> str:
    """Display glyph for an ISO code, falling back to the app-default currency."""
    c = (code or settings.currency or "INR").strip().upper()
    return _SYMBOLS.get(c, c + " ")


def household_currency(db, household_id: int | None) -> str:
    """This household's ISO code. Null on the row means "never chose one" → the app default."""
    from models.tenant import Household

    if db is not None and household_id is not None:
        try:
            code = (db.query(Household.currency)
                    .filter(Household.id == household_id).scalar())
        except Exception:  # noqa: BLE001 — a currency lookup must never break the screen it decorates
            code = None
        if code:
            return str(code).strip().upper()
    return (settings.currency or "INR").upper()


def household_symbol(db, household_id: int | None) -> str:
    """The symbol to prefix this household's amounts with."""
    return symbol_for_code(household_currency(db, household_id))
