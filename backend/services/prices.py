"""Stale-tolerant market price lookup for net-worth.

Uses yfinance when available (delayed/EOD quotes are fine — the user explicitly
accepts stale data). On any failure we return None so callers fall back to the
last cached price or the manual value. FX converts foreign prices to INR.
"""

from __future__ import annotations

import logging

log = logging.getLogger("artha.prices")

# Fallback FX (approx, stale) used only if a live FX quote can't be fetched.
_FX_FALLBACK_INR = {"INR": 1.0, "USD": 83.0, "EUR": 90.0, "GBP": 105.0}


def _yf():
    try:
        import yfinance as yf  # noqa: PLC0415
        return yf
    except Exception:
        return None


def fetch_price(ticker: str) -> tuple[float, str] | None:
    """Return (price, currency) for a Yahoo ticker, or None on failure."""
    yf = _yf()
    if yf is None or not ticker:
        return None
    try:
        t = yf.Ticker(ticker)
        fast = getattr(t, "fast_info", None)
        price = None
        currency = "INR"
        if fast:
            price = fast.get("last_price") or fast.get("lastPrice")
            currency = (fast.get("currency") or "INR").upper()
        if not price:
            hist = t.history(period="5d")
            if not hist.empty:
                price = float(hist["Close"].dropna().iloc[-1])
        if price:
            return (float(price), currency)
    except Exception as e:  # noqa: BLE001
        log.warning("price fetch failed for %s: %s", ticker, e)
    return None


def fx_to_inr(currency: str) -> float:
    """Conversion rate from `currency` to INR (1.0 for INR)."""
    currency = (currency or "INR").upper()
    if currency == "INR":
        return 1.0
    yf = _yf()
    if yf is not None:
        try:
            hist = yf.Ticker(f"{currency}INR=X").history(period="5d")
            if not hist.empty:
                return float(hist["Close"].dropna().iloc[-1])
        except Exception:
            pass
    return _FX_FALLBACK_INR.get(currency, 1.0)


# ── Gold (household jewellery) — resale value of the metal ──
_GOLD_PURITY = {"24k": 1.0, "22k": 0.916, "18k": 0.75}

def fetch_gold_per_gram(purity: str = "24k") -> float | None:
    """INR per gram for the given purity at the INDIAN market rate.

    Base = COMEX spot (GC=F) × USD→INR ÷ 31.1035 g/oz. But Indian physical gold trades well ABOVE
    international spot (import duty + 3% GST + local premium), so we scale by a configurable
    `gold_india_premium` (~1.16) to land on the Indian market price — otherwise net worth understates
    the gold by ~₹10L (the ₹62L-vs-₹72L gap). Set gold_india_premium=1.0 to revert to raw melt/spot.
    """
    r = fetch_price("GC=F")
    if not r:
        return None
    from config import settings
    inr_per_oz = r[0] * fx_to_inr("USD")
    per_g_24k = inr_per_oz / 31.1035 * (settings.gold_india_premium or 1.0)
    return round(per_g_24k * _GOLD_PURITY.get((purity or "24k").lower(), 1.0), 2)
