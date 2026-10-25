"""Read the covering email, not just its attachment.

A bank emails a locked statement and states the key in the same message: "Password abcd1234", or the
rule that builds it ("first 4 letters of the name on the card + date of birth in DDMM"). The fetcher
returned only (filename, bytes) and threw the body away, so 3 real card statements sat unopened while
the app asked the user to configure passwords the email had already supplied.

Nothing here logs or returns a password in a message. Candidates go straight to the PDF opener.
"""

from __future__ import annotations

import re

# Senders that actually issue CARD statements. The statement query also catches broker, depository
# and tax statements, which is most of what arrives and none of what we parse.
CARD_SENDERS = {
    "cc.statements@axis.bank.in": ("Axis", "Credit Card"),
    "credit_cards@icici.bank.in": ("ICICI", "Credit Card"),
    "cardstatement@kotak.bank.in": ("Kotak", "Credit Card"),
    "emailstatements.cards@hdfcbank.net": ("HDFC", "Credit Card"),
    "estatement.cards@hdfcbank.com": ("HDFC", "Credit Card"),
}

# "Password abcd1234", "password is abcd1234", "Password: abcd1234"
_EXPLICIT = re.compile(r"password\s*(?:is|:)?\s*([A-Za-z0-9@#$&]{4,24})\b", re.I)
_NOISE = {"protected", "the", "your", "this", "and", "for", "with", "using", "below", "same", "you"}


def sender_of(from_header: str) -> str:
    m = re.search(r"<([^>]+)>", from_header or "")
    return (m.group(1) if m else (from_header or "")).strip().lower()


# Banks migrate their sending domain and the old address keeps working for a while, so an exact-match
# list goes quietly stale: hdfcbank.net -> hdfcbank.bank.in and icicibank.com -> icici.bank.in both
# happened here, and each one silently dropped a card from discovery.
_ISSUER_DOMAINS = {"hdfcbank": "HDFC", "icicibank": "ICICI", "icici": "ICICI",
                   "kotak": "Kotak", "axis": "Axis", "barodamail": "Bank of Baroda",
                   "bankofbaroda": "Bank of Baroda"}


def card_issuer(from_header: str) -> tuple[str, str] | None:
    """(bank, card_type) when this sender issues card statements, else None.

    Falls back to (issuer domain + card-ish local part) so a domain migration does not drop a card.
    The local part must mention a card: the same banks mail ACCOUNT statements from the same domains
    (estatement@icicibank.com, bankstatements@kotak.bank.in) and those are not card statements — the
    parsers do not read them and counting them made this step look broken when it was working.
    """
    addr = sender_of(from_header)
    known = CARD_SENDERS.get(addr)
    if known:
        return known
    local, _, domain = addr.partition("@")
    if not domain or not ("card" in local or local.startswith("cc.")):
        return None
    for part in domain.split("."):
        if part in _ISSUER_DOMAINS:
            return (_ISSUER_DOMAINS[part], "Credit Card")
    return None


def passwords_from_email(body: str) -> list[str]:
    """Every plausible password the email states, best first.

    Explicit values only. A rule ("first 4 letters + DDMM") is left to parsers/pdf_password_rules,
    which already derives candidates from the household's identity fields — restating that logic here
    would give two implementations to keep in step.
    """
    out: list[str] = []
    for m in _EXPLICIT.finditer(body or ""):
        cand = m.group(1).strip()
        if cand.lower() in _NOISE or cand.isalpha() and len(cand) < 6:
            continue
        if cand not in out:
            out.append(cand)
    return out
