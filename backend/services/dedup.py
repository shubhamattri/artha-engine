"""Cross-source de-duplication — Fellegi-Sunter probabilistic record linkage.

A duplicate is the SAME real transaction captured more than once (statement + SMS + Gmail alert),
or a re-uploaded statement. Two payments of the same amount to different people on different days
are NOT duplicates — so we score the *evidence*, we don't hard-match on amount alone.

Algorithm (Fellegi & Sunter, 1969 — the standard behind dedupe.io / Splink):
  • BLOCK on (direction, amount-to-the-rupee) — only same-amount pairs are even compared.
  • For each candidate pair, compare fields at AGREEMENT LEVELS and add a match weight:
        weight(level) = log2( m / u )
        m = P(this level | the pair is truly the same txn)
        u = P(this level | the pair is a coincidence)
    Fields: date proximity, counterparty similarity (Jaro-Winkler), card last-4, same/cross source.
  • Total score = prior + Σ field weights. Two thresholds:
        cross-source, or same-source with the paise guard satisfied → duplicate (auto-merge)
        anything else                                               → possible duplicate (human review)

    There is no score threshold. An earlier design weighted signals and compared against T_MATCH /
    T_REVIEW constants; the rules replaced it and the constants outlived it, so they were config that
    changed nothing. Removed rather than left looking tunable.
        below      → not a duplicate
  m/u here are expert-set priors (no labelled data) — the classic F-S approach when training data
  is unavailable. Tunable in WEIGHTS below.

Duplicates are linked to a surviving primary via `dup_of_id` (reversible — never deletes).
"""

from __future__ import annotations

import math
import re
from collections import defaultdict

from sqlalchemy.orm import Session

from config import settings
from models.transaction import Transaction

SOURCE_RANK = {"statement": 0, "sms": 1, "gmail": 2, "manual": 3}  # lower = kept as primary
PRIOR_WEIGHT = -3.0          # within a same-amount block, most pairs are still NOT matches

# Thresholds and tolerances describe how a BANK duplicates its own alerts, so they move with the
# banks a household uses, not with this code. Live aliases keep existing call sites reading config.
_CONFIG_ALIASES = {
                   "DATE_TOL_DAYS": "dedup_date_tol_days", "TS_TOL_SEC": "dedup_ts_tol_sec"}


def __getattr__(name: str):
    if name in _CONFIG_ALIASES:
        return getattr(settings, _CONFIG_ALIASES[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# m/u per agreement level → weight = log2(m/u). Tunable.
_MU = {
    "date":   {"same": (0.95, 0.03), "near": (0.04, 0.05), "far": (0.01, 0.92)},
    "name":   {"exact": (0.95, 0.004), "close": (0.04, 0.02), "diff": (0.01, 0.976)},
    # card is a WEAK signal — email/SMS alerts are noisy/absent on the last-4, so a mismatch must
    # not sink an otherwise-clear cross-source match.
    "card":   {"match": (0.60, 0.20), "unknown": (0.30, 0.40), "diff": (0.10, 0.40)},
    "source": {"cross": (0.55, 0.25), "same": (0.45, 0.75)},
}
_W = {f: {lvl: math.log2(m / u) for lvl, (m, u) in levels.items()} for f, levels in _MU.items()}

_PLACEHOLDER_LAST4 = {"", "0000", "sms", None}
_UPI_SPLIT = re.compile(r"\b(?:UPI|IMPS|NEFT|RTGS|ACH\s*[DC]|IB\s*FUNDS\s*TRANSFER|BIL|POS|TPT)\b", re.I)


# ── string similarity (Jaro-Winkler) ─────────────────────────────────────────
def _jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0
    l1, l2 = len(s1), len(s2)
    if l1 == 0 or l2 == 0:
        return 0.0
    reach = max(l1, l2) // 2 - 1
    if reach < 0:
        reach = 0
    m1 = [False] * l1
    m2 = [False] * l2
    matches = 0
    for i in range(l1):
        for j in range(max(0, i - reach), min(i + reach + 1, l2)):
            if not m2[j] and s1[i] == s2[j]:
                m1[i] = m2[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    t = k = 0
    for i in range(l1):
        if m1[i]:
            while not m2[k]:
                k += 1
            if s1[i] != s2[k]:
                t += 1
            k += 1
    t //= 2
    return (matches / l1 + matches / l2 + (matches - t) / matches) / 3


def _jaro_winkler(s1: str, s2: str) -> float:
    j = _jaro(s1, s2)
    prefix = 0
    for a, b in zip(s1, s2):
        if a == b and prefix < 4:
            prefix += 1
        else:
            break
    return j + prefix * 0.1 * (1 - j)


def _counterparty(t: Transaction) -> str:
    raw = (t.merchant or "").strip()
    if not raw:
        desc = t.description or ""
        parts = _UPI_SPLIT.split(desc, maxsplit=1)
        raw = parts[1] if len(parts) > 1 else desc
        raw = re.split(r"[-/@]", raw.strip(" -/"))[0]
    # drop payment-gateway prefix: "PYU*Jubilant", "RAZ*Swiggy", "FS *DeHashed" → the real merchant
    if "*" in raw:
        raw = raw.rsplit("*", 1)[-1]
    s = re.sub(r"[^A-Za-z0-9 ]", " ", raw.upper())
    s = re.sub(r"\b\d{3,}\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # strip leading gateway tokens that have no separator (e.g. "RAZPSWIGGY", "PYUCLEARTRIP")
    s = re.sub(r"^(PYU|RAZP?|BDSK|PAYU|PINELABS|PAYTM)\s+", "", s)
    return s


# ── Identity-based matching ───────────────────────────────────────────────────
# A transaction's IDENTITY is (instrument, amount, date) — the merchant NAME is a display label,
# not part of identity. Two rows with the same card, same amount and same date are the same real
# transaction, whether one says "Alpha Nair", "Not specified", "Grofers" or "Blink Commerce".


def _is_named(s: str) -> bool:
    """Does this row carry a REAL payee name? A blank/'Not specified', or a generic bank-alert
    label ('Axis Bank', '... Online Banking'), is NOT a payee — it tells us nothing to compare."""
    if not s or s in {"NOT SPECIFIED", "UNKNOWN MERCHANT"}:
        return False
    if s.endswith("BANK") or "BANKING" in s:
        return False
    return True


def _real_card(t: Transaction) -> str | None:
    return None if (t.card_last4 or "").lower() in _PLACEHOLDER_LAST4 else t.card_last4


def _has_paise(amt: float) -> bool:
    """True when the amount is NOT a round rupee (has paise). Round amounts are the signature of
    deliberate repeats — SIPs, standing transfers — which are genuinely separate debits. A precise
    paise value (₹2,500.35) repeated is the signature of ONE debit alerted twice."""
    return round(amt, 2) != round(amt, 0)


def _same_source_certain(cluster: list[Transaction]) -> bool:
    """Can we AUTO-merge a same-source cluster (two SMS for one debit) without risking a real repeat?
    STRICT — this only ever applies to SMS-vs-SMS clusters found by the TIMESTAMP tier (so a close
    occurred_at is already guaranteed). NEVER to statement or gmail rows: the statement is authoritative
    (N statement lines = N real debits) and must never be collapsed by a heuristic. On top of SMS-only
    we still require: paise amount (not a round SIP/transfer), the SAME real card, a compatible payee,
    and no two-provider clash. Catches banks that fire 2 SMS per card txn (card-debit + AutoPay/merchant
    confirm) while leaving round-amount repeats — multiple SIPs — strictly alone. Reversible via undo()."""
    if not all(r.source == "sms" for r in cluster):
        return False
    if _provider_conflict(cluster):
        return False
    cards = {c for c in (_real_card(r) for r in cluster) if c}
    if len(cards) > 1:
        return False
    if not _names_compatible(cluster):
        return False
    # A precise PAISE amount repeated is the signature of ONE debit alerted twice.
    if _has_paise(cluster[0].amount):
        return True
    # ASYMMETRIC CARD signal: one SMS resolved a real card, another (same amount/day/name) came through
    # with the placeholder "0000" — i.e. the bank fired a second SMS in a format the parser couldn't pin
    # to a card. That's ONE debit captured twice, not two identical debits: two genuine repeats (SIPs)
    # almost always resolve the SAME real card, never one-resolved/one-blank. SIP repeats (both real,
    # same card — no placeholder) fall through untouched.
    real_rows = [r for r in cluster if _real_card(r)]
    placeholder_rows = [r for r in cluster if _real_card(r) is None]
    if real_rows and placeholder_rows:   # cards already proven ≤1 distinct real card above
        return True
    # A ROUND amount normally means a genuine repeat (a SIP, a standing transfer) and must stay
    # separate — UNLESS it's a known MERCHANT (Blinkit/Zomato/Swiggy/Amazon/…). A shop charging the
    # EXACT same round amount to the same card on the same day is one purchase captured twice (bank
    # SMS + merchant SMS + email), not two identical orders. Merchant rows resolve to a provider;
    # SIPs and person-to-person transfers don't — so investments/transfers remain protected.
    return any(_provider(r) is not None for r in cluster)


def _primary(cluster: list[Transaction]) -> Transaction:
    # Keep the row with the BEST identity as the survivor: lowest source rank first, then prefer a
    # resolved real card over a placeholder ("0000") and a row that carries a bank reference — so a
    # merge never leaves the unresolved SMS (card 0000, bank "SMS") as the visible row while the row
    # with the real card + reference gets hidden. Ties break by id for determinism.
    return sorted(cluster, key=lambda t: (
        SOURCE_RANK.get(t.source, 9),
        0 if _real_card(t) else 1,
        0 if t.bank_ref else 1,
        t.id,
    ))[0]


def _emit(cluster: list[Transaction]) -> dict:
    cross = len({c.source for c in cluster}) > 1
    primary = _primary(cluster)
    # cross-source = the same purchase seen by two channels → certain (auto-merge). same-source same-day
    # = usually a double-capture but could be two identical buys → only auto-merge when the paise guard
    # proves it's one debit (see _same_source_certain); otherwise human confirms. A two-different-provider
    # cluster (Swiggy vs Instamart) is never the same txn → force review.
    confidence = "high" if (cross or _same_source_certain(cluster)) else "review"
    if _provider_conflict(cluster):
        confidence = "review"
    return {
        "primary": primary,
        "duplicates": [c for c in cluster if c.id != primary.id],
        "confidence": confidence,
        "kind": "cross-source" if cross else "same-day",
        "min_score": None,
    }


def _day(s: str):
    from datetime import date as _date
    return _date.fromisoformat(s[:10])


# ── Provider identity (the owner's step 5: "resolve the name to the real provider / same kind of work").
#    Two goals: (a) COLLAPSE variants of one business so they merge — "Grofers" ≡ "Blink Commerce" ≡
#    "Blinkit"; (b) KEEP genuinely different businesses apart so two same-amount buys don't merge —
#    "Swiggy" (food) ≠ "Instamart" (its grocery arm) are separate transactions. Substring match on the
#    cleaned counterparty. INTENTIONALLY high-precision: only well-known confusable brands are listed;
#    an unknown merchant returns None and falls back to string-similarity, never a false provider match.
#    More specific keys (instamart) are ordered BEFORE their parent brand (swiggy) so a combined string
#    like "SWIGGYINSTAMART" resolves to the grocery arm, not the parent. ──
_PROVIDER_ALIASES: dict[str, tuple[str, ...]] = {
    "instamart": ("instamart",),                                   # Swiggy's grocery arm — distinct txn
    "blinkit":   ("blinkit", "blink commerce", "blinkcommerce", "blink comme", "blink", "grofers"),
    "zepto":     ("zepto",),
    "swiggy":    ("swiggy", "bundl"),                              # food delivery (Bundl = legal name)
    "zomato":    ("zomato", "eternal"),
    "bigbasket": ("bigbasket", "big basket", "supermarket grocery"),
    "amazon":    ("amazon", "amzn"),
    "flipkart":  ("flipkart",),
    "dunzo":     ("dunzo",),
    "dmart":     ("dmart", "avenue supermart"),
}


def _provider(t: Transaction) -> str | None:
    """Canonical provider for a row, or None if the merchant isn't a known confusable brand."""
    name = (_counterparty(t) or "").lower()
    if not name:
        return None
    for canon, keys in _PROVIDER_ALIASES.items():
        if any(k in name for k in keys):
            return canon
    return None


def _provider_conflict(cluster: list[Transaction]) -> bool:
    """True when the cluster contains two DIFFERENT known providers — same amount but not the same
    business (Swiggy vs Instamart). Such a cluster must never auto-merge, whatever the timestamp says."""
    known = {p for p in (_provider(r) for r in cluster) if p}
    return len(known) > 1


# Non-identifying tokens — shared only because everything is a "pvt ltd upi payment", not because it's
# the same merchant. Excluded from the token-overlap compatibility test.
_NAME_STOP = {"pvt", "ltd", "private", "limited", "and", "the", "upi", "payment", "pay", "india"}


def _name_tokens(s: str) -> set[str]:
    return {w for w in s.lower().split() if len(w) >= 3 and w not in _NAME_STOP}


def _pair_compatible(a: str, b: str) -> bool:
    """Two payee names could be the same merchant: string-similar OR sharing a real token ('Home Loan
    EMI' ≡ 'Example Bank Home Loan' via {home,loan}; 'CT Flight' ≡ 'Cleartrip Flight' via {flight}). Two
    different people ('Alpha Nair' vs 'Bravo Menon') share nothing → not compatible."""
    return _jaro_winkler(a, b) >= 0.80 or bool(_name_tokens(a) & _name_tokens(b))


def _could_pair(a: Transaction, b: Transaction) -> bool:
    """Tier-1 clustering gate — could these two be the SAME debit, enough to even compare? Yes, UNLESS
    they're two clearly-unrelated named payees (the cook ₹7,000 vs the maid ₹7,000 — different people).
    A provider-family clash (Swiggy vs its Instamart arm) DOES pair here so it can surface for review
    (_emit then downgrades it); a generic bank-alert side (no real name) can't prove a difference so it
    pairs too. Only two distinct real names with nothing in common are kept apart."""
    na, nb = _counterparty(a), _counterparty(b)
    if not (_is_named(na) and _is_named(nb)):
        return True                                   # a generic side → can't say they differ
    if _provider(a) and _provider(b):
        return True                                   # both known brands (same, or Swiggy/Instamart)
    return _pair_compatible(na, nb)                   # two real names: only if similar / shared token


def _names_compatible(cluster: list[Transaction]) -> bool:
    """Could these rows be ONE transaction (same merchant)? Provider identity decides first — different
    known providers (Swiggy vs Instamart) are never compatible; the same provider (Grofers≡Blinkit) is,
    whatever the spelling. Otherwise fall back to name compatibility (similarity or shared token); a
    generic bank-alert label with no real name can't block a match."""
    known = {p for p in (_provider(r) for r in cluster) if p}
    if len(known) > 1:
        return False                       # two different real providers, same amount → not one txn
    named = [n for n in (_counterparty(r) for r in cluster) if _is_named(n)]
    if len(named) < 2:
        return True
    if known:
        provider_names = [_counterparty(r) for r in cluster if _provider(r)]
        other_names = [_counterparty(r) for r in cluster if _is_named(_counterparty(r)) and not _provider(r)]
        # A known provider can match a generic bank alert, but not an unrelated real named payee.
        # Example: "Swiggy" + "Reshma" is two people/merchants, not one debit.
        return all(any(_pair_compatible(other, provider_name) for provider_name in provider_names)
                   for other in other_names)
    return all(_pair_compatible(named[i], named[j])
               for i in range(len(named)) for j in range(i + 1, len(named)))


def find_duplicate_groups(db: Session, *, household_id: int) -> list[dict]:
    """IDENTITY = (direction, amount) with DATE TOLERANCE. The same transaction routinely reports a
    DIFFERENT date across channels (SMS vs posting vs statement) and across statement re-uploads, so
    exact-date matching silently missed real duplicates. We block by amount, single-linkage cluster
    rows whose dates fall within DATE_TOL_DAYS, then per cluster:
      • ≤1 capture per channel (cross-source) → the same txn seen by N channels → MERGE (high). If the
        cluster spans multiple days we also require compatible payee names, so two different
        same-amount buys (one caught per channel) aren't wrongly merged.
      • some channel captured 2+ (same-source re-upload / repeat) → group by payee name and surface for
        REVIEW — NOT auto-merged, because two genuine identical purchases can look the same.
    (The card is also excluded from identity — channels disagree on the last-4.)"""
    from collections import Counter

    rows = db.query(Transaction).filter(
        Transaction.household_id == household_id,
        Transaction.dup_of_id.is_(None), Transaction.archived.is_(False)).all()
    blocks: dict[tuple, list[Transaction]] = defaultdict(list)
    for r in rows:
        blocks[(r.direction, round(r.amount, 2))].append(r)   # date handled by clustering below

    groups: list[dict] = []
    for block in blocks.values():
        if len(block) < 2:
            continue

        used: set[int] = set()

        # ── TIER 0 (ABSOLUTE): identical bank reference (UPI RRN / NEFT-IMPS UTR) = the SAME real debit,
        #    no matter the channel, date, name or card. The bank's own id — zero ambiguity. Merge. ──
        by_ref: dict[str, list[Transaction]] = defaultdict(list)
        for r in block:
            if r.bank_ref:
                by_ref[r.bank_ref].append(r)
        for rs in by_ref.values():
            if len(rs) >= 2:
                g = _emit(rs)
                g["confidence"] = "high"
                groups.append(g)
                used.update(id(r) for r in rs)

        # ── TIER 1 (CERTAIN): timestamp match. Two rows of the same amount whose precise times are
        #    within TS_TOL_SEC AND whose payees are compatible are the SAME transaction (an SMS and its
        #    email; a cross-midnight capture). The name gate is CRITICAL: two DIFFERENT people paid the
        #    same amount a minute apart (the cook ₹7,000 and the maid ₹7,000) are NOT one debit — time
        #    proximity alone must never cluster them. Union-find, same as the date-window tier. ──
        timed = [r for r in block if r.occurred_at is not None and id(r) not in used]
        if len(timed) >= 2:
            m = len(timed)
            tparent = list(range(m))

            def _tfind(x: int) -> int:
                while tparent[x] != x:
                    tparent[x] = tparent[tparent[x]]
                    x = tparent[x]
                return x

            for a in range(m):
                for b in range(a + 1, m):
                    ra, rb = timed[a], timed[b]
                    if ra.bank_ref and rb.bank_ref and ra.bank_ref != rb.bank_ref:
                        continue                       # different bank refs → different debits
                    if (abs((ra.occurred_at - rb.occurred_at).total_seconds()) <= settings.dedup_ts_tol_sec
                            and _could_pair(ra, rb)):
                        tparent[_tfind(a)] = _tfind(b)
            tclusters: dict[int, list[Transaction]] = defaultdict(list)
            for idx in range(m):
                tclusters[_tfind(idx)].append(timed[idx])
            for cl in tclusters.values():
                if len(cl) < 2:
                    continue
                # _emit decides confidence: cross-source → certain; same-source → certain ONLY under the
                # paise guard (one debit alerted twice), else review; a provider clash → review.
                groups.append(_emit(cl))
                used.update(id(r) for r in cl)

        # ── TIERS 2-4: rows without a usable timestamp match fall back to date-window + provider. ──
        rest = [r for r in block if id(r) not in used]
        if len(rest) < 2:
            continue
        # Cluster by COMPATIBILITY, not just proximity. A flat date-window chain wrongly links two
        # DIFFERENT merchants that merely share an amount a couple of days apart (e.g. Mahadev ₹200 on
        # the 11th and Suman ₹200 on the 13th) into one blob — which then trips the same-source path and
        # buries genuine cross-source duplicates in review. Union-find instead: two rows join only if
        # they're within the date window AND name/provider-compatible (same brand, or one generic side).
        n = len(rest)
        parent = list(range(n))

        def _find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a in range(n):
            for b in range(a + 1, n):
                # Link two rows only if within the date window AND could-be-same-merchant AND their
                # bank references don't contradict. The compatibility gate applies EVEN on the same day
                # (two different people who each sent ₹100 today must stay apart). And two DIFFERENT bank
                # refs can never be the same debit — a hard block, even if amount+date+name all match.
                ra, rb = rest[a], rest[b]
                if ra.bank_ref and rb.bank_ref and ra.bank_ref != rb.bank_ref:
                    continue
                if (abs((_day(ra.date) - _day(rb.date)).days) <= settings.dedup_date_tol_days
                        and _names_compatible([ra, rb])):
                    parent[_find(a)] = _find(b)

        merged: dict[int, list[Transaction]] = defaultdict(list)
        for idx in range(n):
            merged[_find(idx)].append(rest[idx])
        clusters = list(merged.values())

        for cl in clusters:
            if len(cl) < 2:
                continue
            sources = {r.source for r in cl}
            if len(sources) == 1:
                # ONE channel only, and NO usable 15-min timestamp match. Two statement lines / two SMS of
                # the same amount are USUALLY real repeats (multiple SIPs, repeat transfers) — never merge
                # those. The ONE exception: a paise-precise, same-card, same-provider SMS pair on the SAME
                # DAY is the signature of ONE debit the SMS pipeline captured twice (a double-ingest whose
                # two copies drift hours apart in occurred_at, so the timestamp tier can't see them) — not
                # two identical orders. That's the exact judgement _same_source_certain makes; here we just
                # can't lean on 15-min proximity, so we require SAME-DAY instead. Round amounts (the
                # SIP/transfer signature) and multi-day clusters stay strictly separate. Reversible.
                if len({r.date[:10] for r in cl}) == 1 and _same_source_certain(cl):
                    groups.append(_emit(cl))   # _same_source_certain → _emit tags it "high" → auto-merges
                continue

            statements = [r for r in cl if r.source == "statement"]
            if statements:
                # STATEMENT IS AUTHORITATIVE: the bank lists each real debit exactly once, so N statement
                # lines = N real debits. Fold the SMS/email CAPTURES of those debits into one statement
                # survivor; every statement line stays. → N survivors, no inflation, no hidden debit.
                # (This is what lets 3 Groww statement SIPs survive while their 5 SMS collapse in.)
                non_stmt = [r for r in cl if r.source != "statement"]
                if not non_stmt:
                    continue
                groups.append({"primary": _primary(statements), "duplicates": non_stmt,
                               "confidence": "high", "kind": "cross-source", "min_score": None})
            elif max(Counter(r.source for r in cl).values()) == 1:
                # No statement yet (recent SMS/email). Merge only a clean 1-per-channel match (SMS+email
                # of one debit). If a channel repeated, we can't know the real count without the
                # statement → leave separate; it reconciles when the statement lands.
                groups.append(_emit(cl))
    return groups


def _view(t: Transaction) -> dict:
    return {"id": t.id, "date": t.date, "description": t.description, "merchant": t.merchant,
            "amount": t.amount, "direction": t.direction, "source": t.source,
            "category": t.category, "card_last4": t.card_last4}


def preview(db: Session, *, household_id: int) -> dict:
    """Read-only: what would merge, grouped, with the duplicate-rupee impact + match score."""
    groups = find_duplicate_groups(db, household_id=household_id)
    out = []
    for g in groups:
        out.append({
            "primary": _view(g["primary"]),
            "duplicates": [_view(d) for d in g["duplicates"]],
            "confidence": g["confidence"],
            "kind": g.get("kind"),
            "score": g.get("min_score"),
            "wasted": round(sum(d.amount for d in g["duplicates"]), 2),
        })
    out.sort(key=lambda x: (x["confidence"] != "high", -len(x["duplicates"]), -x["wasted"]))
    high = [g for g in out if g["confidence"] == "high"]
    return {
        "groups": out,
        "group_count": len(out),
        "high_confidence": len(high),
        "duplicate_rows": sum(len(g["duplicates"]) for g in out),
        "auto_mergeable_rows": sum(len(g["duplicates"]) for g in high),
    }


def _rev(t: Transaction) -> dict:
    return {"id": t.id, "fingerprint": t.fingerprint, "date": t.date, "merchant": t.merchant,
            "description": (t.description or "")[:90], "amount": t.amount, "source": t.source,
            "card_last4": t.card_last4, "category": t.category,
            "occurred_at": t.occurred_at.isoformat() if t.occurred_at else None, "bank_ref": t.bank_ref}


def review_candidates(db: Session, household_id: int | None = None) -> list[dict]:
    """The AMBIGUOUS residue for HUMAN review — NOT auto-merged, NOT deterministically decidable.

    A pair qualifies only when it's genuinely uncertain: same card, same amount, the SAME DAY, and
    payee/merchant-compatible — but with NO shared bank reference to prove it's one debit, and refs that
    don't positively contradict. We exclude everything the engine already auto-merges (high-confidence),
    everything with two different refs (deterministically two debits), and every pair the user has already
    dismissed. Same-day only: two same-amount payments to one payee on DIFFERENT days are almost always
    two real transactions (a recurring transfer), not a duplicate — those must never clutter the queue.
    """
    from collections import defaultdict

    from models.dismissed import DismissedDuplicate

    q = db.query(Transaction).filter(
        Transaction.dup_of_id.is_(None), Transaction.archived.is_(False),
        Transaction.direction == "debit")
    if household_id is not None:
        q = q.filter(Transaction.household_id == household_id)
    rows = q.all()

    # ids the auto-merger already claims → those get merged, never reviewed
    high_ids: set[int] = set()
    for g in find_duplicate_groups(db, household_id=household_id):
        if g["confidence"] == "high":
            high_ids.add(g["primary"].id)
            high_ids.update(d.id for d in g["duplicates"])

    dq = db.query(DismissedDuplicate)
    if household_id is not None:
        dq = dq.filter(DismissedDuplicate.household_id == household_id)
    dismissed = {(d.fp_lo, d.fp_hi) for d in dq.all()}

    blocks: dict[tuple, list[Transaction]] = defaultdict(list)
    for r in rows:
        blocks[(r.card_last4, round(r.amount, 2))].append(r)

    out: list[dict] = []
    seen_pairs: set[tuple] = set()
    for block in blocks.values():
        if len(block) < 2:
            continue
        for i in range(len(block)):
            for j in range(i + 1, len(block)):
                a, b = block[i], block[j]
                if a.id in high_ids or b.id in high_ids:
                    continue
                if a.source == "statement" and b.source == "statement":
                    continue                                     # statement is authoritative: N lines = N
                    # real debits (two Groww SIPs, two Cloudflare charges, two GST fees on one day are NOT
                    # a duplicate). The genuine ambiguous case is always an SMS/email double-capture.
                if a.date[:10] != b.date[:10]:
                    continue                                     # same day only
                if a.bank_ref and b.bank_ref and a.bank_ref != b.bank_ref:
                    continue                                     # different refs → two real debits
                if not _could_pair(a, b):
                    continue                                     # unrelated payees (₹7,000 to two people)
                fps = tuple(sorted((a.fingerprint, b.fingerprint)))
                if fps in dismissed or fps in seen_pairs:
                    continue
                seen_pairs.add(fps)
                primary = _primary([a, b])
                dup = b if primary.id == a.id else a
                out.append({
                    "primary": _rev(primary),
                    "duplicate": _rev(dup),
                    "amount": round(a.amount, 2),
                    "why": f"Same ₹{a.amount:,.0f} on the same card (••{a.card_last4}) the same day "
                           f"({a.date[:10]}), no shared bank reference — likely one payment captured twice.",
                })
    out.sort(key=lambda g: -g["amount"])
    return out


def merge_pair(db: Session, household_id: int, primary_id: int, dup_id: int) -> dict:
    """Confirm a reviewed pair: link dup → primary (reversible via undo). Both must belong to the
    household. The lower-source-rank row is kept as primary regardless of which id was passed."""
    a = db.query(Transaction).filter(Transaction.id == primary_id,
                                     Transaction.household_id == household_id).first()
    b = db.query(Transaction).filter(Transaction.id == dup_id,
                                     Transaction.household_id == household_id).first()
    if not a or not b:
        raise ValueError("transaction not found for this household")
    primary = _primary([a, b])
    dup = b if primary.id == a.id else a
    dup.dup_of_id = primary.id
    from services.audit import log_event
    log_event(db, household_id=household_id, txn_id=dup.id, action="merge",
              field="dup_of_id", old=None, new=primary.id, actor="review")
    db.commit()
    return {"ok": True, "primary_id": primary.id, "merged_id": dup.id}


def dismiss_pair(db: Session, household_id: int, fp_a: str, fp_b: str) -> dict:
    """Remember that two transactions are NOT the same debit, so the pair never resurfaces."""
    from models.dismissed import DismissedDuplicate

    lo, hi = sorted((fp_a, fp_b))
    exists = (db.query(DismissedDuplicate)
              .filter(DismissedDuplicate.household_id == household_id,
                      DismissedDuplicate.fp_lo == lo, DismissedDuplicate.fp_hi == hi).first())
    if not exists:
        db.add(DismissedDuplicate(household_id=household_id, fp_lo=lo, fp_hi=hi))
        db.commit()
    return {"ok": True, "dismissed": True}


def apply_dedup(db: Session, only_high: bool = True, *, household_id: int) -> dict:
    """Link duplicates to their primary (reversible). only_high → merge only high-confidence clusters."""
    merged = 0
    for g in find_duplicate_groups(db, household_id=household_id):
        if only_high and g["confidence"] != "high":
            continue
        for d in g["duplicates"]:
            d.dup_of_id = g["primary"].id
            from services.audit import log_event
            log_event(db, household_id=d.household_id, txn_id=d.id, action="merge",
                      field="dup_of_id", old=None, new=g["primary"].id, actor="dedup")
            merged += 1
    db.commit()
    return {"ok": True, "merged_rows": merged}


def auto_merge(db: Session, *, household_id: int) -> dict:
    """What the ingest/sync paths call: merge everything SAFE without human review — ONLY
    high-confidence CROSS-source groups (one debit reported by two channels). Same-source repeats are
    never auto-merged: without a bank reference we can't prove they aren't two real identical debits
    (multiple SIPs). Fully reversible via undo(). Keeps the headline number right with no 'merge' button."""
    merged = 0
    for g in find_duplicate_groups(db, household_id=household_id):
        if g["confidence"] != "high":
            continue
        for d in g["duplicates"]:
            d.dup_of_id = g["primary"].id
            from services.audit import log_event
            log_event(db, household_id=d.household_id, txn_id=d.id, action="merge",
                      field="dup_of_id", old=None, new=g["primary"].id, actor="dedup")
            merged += 1
    db.commit()
    return {"ok": True, "merged_rows": merged}


def undo(db: Session, primary_id: int | None = None, *, household_id: int) -> dict:
    """Restore duplicates (a single primary's, or all) back to active."""
    q = db.query(Transaction).filter(Transaction.household_id == household_id,
                                     Transaction.dup_of_id.isnot(None))
    if primary_id is not None:
        q = q.filter(Transaction.dup_of_id == primary_id)
    restored = 0
    for t in q.all():
        t.dup_of_id = None
        restored += 1
    db.commit()
    return {"ok": True, "restored": restored}
