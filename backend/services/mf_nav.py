"""Mutual-fund NAV lookup — makes MF holdings market-priced like stocks.

Source: mfapi.in (a free JSON wrapper over AMFI's official daily NAV feed). Only a
public scheme code is sent — never any personal data. Stale-tolerant: on any
failure we return None and the caller falls back to the last cached NAV.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from datetime import date

log = logging.getLogger("artha.mf_nav")

_SEARCH = "https://api.mfapi.in/mf/search?q="
# Dividend variants of a direct plan. Holding one when you meant growth changes what the money does.
_PAYOUT = re.compile(r"\b(IDCW|Dividend|Payout|Reinvest)", re.IGNORECASE)
_LATEST = "https://api.mfapi.in/mf/{code}/latest"
_HISTORY = "https://api.mfapi.in/mf/{code}"

_HISTORY_TTL = 6 * 3600          # a closed day's NAV never changes; only the tail grows
_history_cache: dict[str, tuple[float, list[tuple[date, float]]]] = {}


def fetch_nav(scheme_code: str | int) -> tuple[float, str] | None:
    """Return (nav, date_str) for an AMFI scheme code, or None on failure."""
    if not scheme_code:
        return None
    try:
        url = _LATEST.format(code=scheme_code)
        with urllib.request.urlopen(url, timeout=15) as r:
            payload = json.load(r)
        data = payload.get("data") or []
        if not data:
            return None
        nav = float(data[0]["nav"])
        return (nav, data[0].get("date", ""))
    except Exception as e:  # noqa: BLE001
        log.warning("NAV fetch failed for %s: %s", scheme_code, e)
        return None


def clear_history_cache() -> None:
    _history_cache.clear()


def _history(scheme_code: str | int) -> list[tuple[date, float]]:
    """Full NAV history, newest first, cached per scheme. Empty list on failure."""
    key = str(scheme_code)
    hit = _history_cache.get(key)
    if hit and time.time() - hit[0] < _HISTORY_TTL:
        return hit[1]
    try:
        with urllib.request.urlopen(_HISTORY.format(code=key), timeout=15) as r:
            payload = json.load(r)
        series = []
        for row in payload.get("data") or []:
            d, m, y = row["date"].split("-")
            series.append((date(int(y), int(m), int(d)), float(row["nav"])))
        series.sort(key=lambda x: x[0], reverse=True)
    except Exception as e:  # noqa: BLE001
        log.warning("NAV history fetch failed for %s: %s", key, e)
        return []
    _history_cache[key] = (time.time(), series)
    return series


def fetch_nav_on(scheme_code: str | int, on: date) -> tuple[float, str] | None:
    """NAV for a specific date, or the most recent one BEFORE it — markets close on weekends and
    holidays, so exact-match-only would reject perfectly valid dates. None if nothing precedes it."""
    if not scheme_code:
        return None
    for d, nav in _history(scheme_code):
        if d <= on:
            return (nav, d.isoformat())
    return None


_META_TTL = 7 * 24 * 3600        # a scheme's ISIN does not change; only re-look-up weekly
_meta_cache: dict[str, tuple[float, dict]] = {}


def fetch_meta(scheme_code: str | int) -> dict | None:
    """The feed's own metadata for a scheme — fund house, category, and the ISINs.

    Needed to join an AMFI scheme code to a CAS statement, which identifies holdings by ISIN. Cached
    for a week because this is reference data, not a price.
    """
    if not scheme_code:
        return None
    key = str(scheme_code)
    hit = _meta_cache.get(key)
    if hit and time.time() - hit[0] < _META_TTL:
        return hit[1]
    try:
        with urllib.request.urlopen(_LATEST.format(code=key), timeout=15) as r:
            meta = (json.load(r) or {}).get("meta") or {}
    except Exception as e:  # noqa: BLE001
        log.warning("meta fetch failed for %s: %s", key, e)
        return None
    _meta_cache[key] = (time.time(), meta)
    return meta


def search_schemes(query: str, direct_growth_only: bool = True) -> list[dict]:
    """Resolve a fund name to candidate AMFI scheme codes (for the add-fund flow)."""
    try:
        with urllib.request.urlopen(_SEARCH + urllib.parse.quote(query), timeout=15) as r:
            rows = json.load(r)
        out = []
        for x in rows:
            name = x["schemeName"]
            # AMFI does not always print "Growth": some AMCs name the growth plan just "- Direct Plan"
            # and give the payout option a different name. Exclude the payouts, don't demand the word.
            if direct_growth_only and ("Direct" not in name or _PAYOUT.search(name)):
                continue
            out.append({"scheme_code": x["schemeCode"], "scheme_name": name})
        return out[:8]
    except Exception as e:  # noqa: BLE001
        log.warning("scheme search failed for %r: %s", query, e)
        return []
