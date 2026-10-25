"""Two-ledger spend split (Cowork model).

A household's card spend is two very different things mashed into one number:

  * RECURRING living spend — many small buys, each below the threshold
    (groceries, food, fuel, subscriptions). This is the controllable monthly burn.
  * ONE-OFF / big-ticket capital purchases — cars-on-card, IKEA/furniture,
    jewellery, university fees, Apple. A handful of large hits that inflate "Others"
    and make the recurring picture look worse than it is.

Splitting them lets the dashboard show the real recurring burn separately from
the lumpy capital purchases. The rule is deliberately simple: a single rupee
threshold on the per-transaction amount.
"""

from __future__ import annotations

# A single SPEND transaction at or above this is treated as a one-off/big-ticket
# capital purchase; below it is recurring living spend.
THRESHOLD = 40000


def recurrence(amount: float, txn_type: str) -> str:
    """Classify a transaction's recurrence bucket.

    Returns:
        'na'        if the transaction is not a SPEND (EMI/PAYMENT/etc. — the
                    recurring/one-off split only applies to genuine spend),
        'oneoff'    if it is a SPEND of THRESHOLD or more (big-ticket capital),
        'recurring' if it is a SPEND below THRESHOLD (normal living spend).
    """
    if txn_type != "SPEND":
        return "na"
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        # Defensive: a malformed amount can't be bucketed — treat as recurring
        # rather than crash (mirrors the parsers' skip-bad-rows posture).
        return "recurring"
    return "oneoff" if amt >= THRESHOLD else "recurring"
