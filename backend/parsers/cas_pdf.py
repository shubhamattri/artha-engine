"""Read a CAMS/KFintech Consolidated Account Statement into per-scheme transaction history.

A CAS is the only place a household's complete mutual-fund history exists in one file: every purchase,
every redemption, the NAV each one transacted at, and the running unit balance, across every RTA. It
is what makes a real XIRR possible — the app's own ledger starts whenever a fund was last anchored,
which is not the same thing as when the money went in.

## The layout, as it actually is

The statement is a flat text stream of scheme blocks. Each block opens with a folio line and closes
with a closing-balance line:

    Folio No: 182967 / 22  PAN: XXXXXXXXXX  KYC: OK  PAN: OK
    <holder name>
    ABCDEF-Some Fund Direct Plan Growth (Non-Demat) - ISIN: INF579M01902(Advisory)
    Opening Unit Balance: 0.000
    01-Jan-2021 Purchase-BSE - 5,000.00 200.000 25.0000 200.000
    16-Apr-2021 *** Stamp Duty *** 0.25
    10-May-2021 *Redemption -BSE - - N1342... , less STT (4,000.00) (153.455) 26.0666 42.353
    Closing Unit Balance: 0.000 NAV on 17-Aug-2026: INR 56.1228 Total Cost Value: 0.00 ...

Three things about that shape drive this parser.

**The last four numbers are the transaction.** Amount, units, NAV, closing balance, in that order, and
every one of them carries a decimal point. Reading positionally from the right is what makes the
description safe to ignore — descriptions vary per RTA, per exchange and per fund house, and matching
on them would break on the next format tweak. An identifier like `N1342...` in the middle never looks
like a number because it has no decimal.

**Parentheses mean negative.** A redemption is written `(4,000.00) (153.455)`. Preserving that sign is
the whole point: a redemption read as a purchase inflates the invested base and flatters the return.

**Lines wrapped in `***` are not transactions.** Stamp duty, STT, address updates, cancellations. They
carry a number, so a naive number-hunting parser picks them up as tiny purchases.

## PAN

The folio line contains the holder's PAN. It is never read, never returned and never logged. The join
key here is the ISIN, which is public.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

import pdfplumber

log = logging.getLogger("artha.cas")

# A money/unit/NAV token. The decimal point is load-bearing: it is what separates a figure from a
# reference number like N134211504373521, which appears mid-description on redemption lines.
_NUM = re.compile(r"\(?-?[\d,]+\.\d+\)?")
_TXN_LINE = re.compile(r"^(\d{2}-[A-Za-z]{3}-\d{4})\s+(.*)$")
_ISIN = re.compile(r"ISIN:\s*([A-Z]{2}[A-Z0-9]{9}\d)")
_FOLIO = re.compile(r"Folio No:\s*([\w/\- ]+?)\s{2,}|Folio No:\s*([\w/\- ]+?)\s+PAN")
_OPENING = re.compile(r"Opening Unit Balance:\s*([\d,]+\.\d+)")
_CLOSING = re.compile(r"Closing Unit Balance:\s*([\d,]+\.\d+)")
_NAV_ON = re.compile(r"NAV on (\d{2}-[A-Za-z]{3}-\d{4}):\s*INR\s*([\d,]+\.\d+)")
# Every RTA writes the scheme line as CODE-Name … - ISIN: …
_SCHEME = re.compile(r"^([A-Z0-9]{4,12})-(.+?)\s*-\s*ISIN:", re.IGNORECASE)


def _num(tok: str) -> float:
    """A CAS figure. Parentheses are the statement's minus sign."""
    neg = tok.startswith("(") and tok.endswith(")")
    v = float(tok.strip("()").replace(",", ""))
    return -v if neg else v


def _iso(d: str) -> str:
    return datetime.strptime(d, "%d-%b-%Y").date().isoformat()


@dataclass
class CasTransaction:
    date: str            # yyyy-mm-dd
    description: str
    amount: float        # negative = money came back out
    units: float         # negative = units left the folio
    nav: float
    closing_units: float

    @property
    def is_redemption(self) -> bool:
        return self.amount < 0


@dataclass
class CasScheme:
    isin: str
    scheme_name: str
    folio: str | None
    opening_units: float
    closing_units: float
    nav: float | None
    nav_date: str | None
    transactions: list[CasTransaction] = field(default_factory=list)

    @property
    def invested_net(self) -> float:
        """Purchases minus redemptions — what the household actually has in, by its own statement."""
        return round(sum(t.amount for t in self.transactions), 2)

    def units_reconcile(self, tol: float = 0.001) -> bool:
        """Opening plus every unit movement should land on the closing balance the RTA printed.

        This is the parser's own proof. A missed line, a dropped sign or a mis-read column all show up
        here, and silently wrong history is worse than no history — it would be laundered into an XIRR
        and quoted as fact.
        """
        walked = self.opening_units + sum(t.units for t in self.transactions)
        return abs(walked - self.closing_units) <= tol


def _finish(cur: dict | None, out: list[CasScheme]) -> None:
    if cur and cur.get("isin"):
        out.append(CasScheme(
            isin=cur["isin"], scheme_name=cur.get("name", ""), folio=cur.get("folio"),
            opening_units=cur.get("opening", 0.0), closing_units=cur.get("closing", 0.0),
            nav=cur.get("nav"), nav_date=cur.get("nav_date"), transactions=cur["txns"],
        ))


def parse_text(text: str) -> list[CasScheme]:
    """Split a CAS text stream into schemes. Separated from PDF reading so it is testable on a
    fixture without a binary, and so a layout change can be diagnosed from text alone."""
    out: list[CasScheme] = []
    cur: dict | None = None
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue

        if line.startswith("Folio No:"):
            _finish(cur, out)
            m = _FOLIO.search(line)
            folio = next((g.strip() for g in (m.groups() if m else ()) if g), None) if m else None
            cur = {"folio": folio, "txns": [], "opening": 0.0, "closing": 0.0}
            continue
        if cur is None:
            continue

        if "ISIN:" in line and not cur.get("isin"):
            mi = _ISIN.search(line)
            if mi:
                cur["isin"] = mi.group(1)
                ms = _SCHEME.match(line)
                cur["name"] = (ms.group(2).strip() if ms else line.split("- ISIN:")[0].strip())
            continue

        if (mo := _OPENING.search(line)):
            cur["opening"] = _num(mo.group(1))
            continue
        if (mc := _CLOSING.search(line)):
            cur["closing"] = _num(mc.group(1))
            if (mn := _NAV_ON.search(line)):
                cur["nav_date"], cur["nav"] = _iso(mn.group(1)), _num(mn.group(2))
            continue

        mt = _TXN_LINE.match(line)
        if not mt:
            continue
        rest = mt.group(2)
        if "***" in rest:            # stamp duty, STT, address updates, cancellations
            continue
        nums = _NUM.findall(rest)
        if len(nums) < 4:            # a note, not a movement
            continue
        amount, units, nav, closing = (_num(n) for n in nums[-4:])
        if units == 0:               # a load or tax adjustment that moved no units
            continue
        desc = rest[: rest.find(nums[-4])].strip(" -,") if nums[-4] in rest else rest.strip()
        cur["txns"].append(CasTransaction(
            date=_iso(mt.group(1)), description=desc, amount=amount,
            units=units, nav=nav, closing_units=closing,
        ))

    _finish(cur, out)
    return out


def parse_cas(path: str, password: str | None = None) -> list[CasScheme]:
    """Read a CAS PDF. `password` is used and discarded; it is never logged or returned."""
    with pdfplumber.open(path, password=password or "") as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    schemes = parse_text(text)
    bad = [s for s in schemes if not s.units_reconcile()]
    if bad:
        log.warning("CAS: %d of %d schemes do not reconcile on units", len(bad), len(schemes))
    return schemes
