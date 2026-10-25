"""Deterministic Number Verifier — T7 in the Artha advisor eval pipeline.

WHY THIS EXISTS: the LLM advisor calls T3 tools that return ground truth from the DB,
but the model can still misquote a figure in its final prose — most dangerously by
presenting the gross variable-spend POOL as "free cash" while the household is already
over budget (the ₹1,50,000 bug class: pool=₹1,50,000, spent=₹1,45,000,
actually_free≈₹5,000, advisor said "you have ₹1,50,000 free — go buy it").

This module re-runs every deterministic compute function from scratch and checks:

  (a) HALLUCINATED_NUMBER  — any INR figure in cited_numbers doesn't match the fresh
      DB recompute within tolerance.

  (b) GROSS_POOL_AS_FREE  — any sentence containing "free / spare / available /
      headroom" cites a ₹ figure that matches the gross pool (cashflow.leftover)
      instead of actually_free = pool − spent_this_period.  Only flags when the pool
      and actually_free differ by more than the text tolerance (i.e. when it matters),
      AND when the sentence does NOT also correctly cite actually_free (honest-contrast
      sentences like "pool is ₹1,50,000 but only ₹5,000 is actually free" are exempt).

  (c) CONTRADICTION  — the answer simultaneously claims "over budget" AND "plenty
      of free cash / headroom". Pure text check — no DB needed.

No LLM. No network. Pure Python + the same deterministic engines the advisor calls.
Called by T6 runner (evals/runner.py) after each advisor_agent.reply(); its output
feeds both the safety gate (passed=False → fail P0) and T9 coach gather().

Public API:
    check_numbers(answer_text, cited_numbers, tools_called, db, household_id) -> dict
        Returns {violations: [...], passed: bool}
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("artha.evals.number_check")

# ── Tolerance ────────────────────────────────────────────────────────────────
# cited_numbers: agent declared it used these exact tool-output values — tight.
_CITED_ABS = 2.0        # ₹2 absolute (covers floating-point round-trip)
_CITED_REL = 0.005      # 0.5% relative

# Text-extracted figures: the model may display-round (₹1,50,000 → "~₹1.5L").
# Loose tolerance avoids false positives for rounded prose.
_TEXT_ABS = 100.0       # ₹100 absolute
_TEXT_REL = 0.02        # 2% relative

# ── Regex patterns ───────────────────────────────────────────────────────────
# Standard ₹ figure: ₹1,50,000 or ₹5,000.00 or ₹3,000 (commas stripped)
_INR_RE = re.compile(r"₹\s*([\d,]+(?:\.\d{1,2})?)")

# Shorthand-AWARE figure: ₹1.5 lakh / ₹1.5L / ₹2.5 Cr / ₹50k as well as plain ₹1,50,000.
# WHY: the ₹150k bug rephrased idiomatically ("you have ₹1.5 lakh free") must NOT slip past
# the free-cash check — the plain _INR_RE would read it as ₹1.7 and never match the pool.
# The unit is an OPTIONAL trailing group; \b keeps a bare "l" in "₹100 loan" from being read
# as lakh (unit backtracks to empty, value stays ₹100).
_INR_UNIT_RE = re.compile(
    r"₹\s*([\d,]+(?:\.\d{1,3})?)\s*(lakhs?|lacs?|crores?|cr|l|k)?\b", re.IGNORECASE)
_UNIT_MULT = {"": 1.0, "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
              "cr": 1e7, "crore": 1e7, "crores": 1e7, "k": 1e3}


def _extract_rupee_figures(text: str) -> list[float]:
    """All ₹ amounts in `text` as absolute rupees, expanding lakh/crore/k shorthand.
    '₹1.5 lakh'→150000, '₹2.5 Cr'→25000000, '₹50k'→50000, '₹1,50,000'→150000."""
    out: list[float] = []
    for num, unit in _INR_UNIT_RE.findall(text):
        try:
            val = _parse_inr(num)
        except ValueError:
            continue
        out.append(val * _UNIT_MULT.get((unit or "").lower(), 1.0))
    return out

# Signals that the sentence is quantifying *available* cash to spend.
# Intentionally conservative — everyday phrases like "pool" and "budget" are NOT here.
_FREE_WORDS_RE = re.compile(
    r"\b(?:free|spare|available|headroom|can\s+spend|left\s+to\s+spend|"
    r"still\s+(?:have|left)\b|remaining\b|breathing\s+room|"
    r"buffer\b|uncommitted|unspent|at\s+your\s+disposal)\b",
    re.IGNORECASE,
)

# Magnitude signals — "plenty" only matters alongside a free-cash word above.
_PLENTY_RE = re.compile(
    r"\b(?:plenty|a\s+lot|lots|ample|significant(?:ly)?|good\s+amount|"
    r"more\s+than\s+enough|well\s+within|comfortably|no\s+problem|"
    r"easily\s+afford|room\s+to)\b",
    re.IGNORECASE,
)

# Phrases that signal the household is over-budget.
_OVER_BUDGET_RE = re.compile(
    r"\b(?:over\s+budget|over\s+your\s+budget|over\s+the\s+budget|"
    r"exceeded?(?:\s+your)?|gone\s+over|overspent|over-budget|"
    r"above\s+budget|past\s+your\s+budget|breached?(?:\s+your)?\s+budget|"
    r"you(?:'ve|\s+have)\s+(?:already\s+)?exceeded)\b",
    re.IGNORECASE,
)

# ── Tool → canonical key map ─────────────────────────────────────────────────
# Maps (tool_name, cited_numbers.label) to the canonical dict key from
# _build_ground_truth().  Only fields we can recompute deterministically.
_TOOL_FIELD_MAP: dict[tuple[str, str], str] = {
    # actually_free tool outputs
    ("actually_free", "pool_inr"):              "pool",
    ("actually_free", "spent_this_period_inr"): "spent",
    ("actually_free", "actually_free_inr"):     "actually_free",
    # cashflow_snapshot tool outputs
    ("cashflow_snapshot", "income_inr"):        "income",
    ("cashflow_snapshot", "emi_inr"):           "emi",
    ("cashflow_snapshot", "fixed_inr"):         "fixed",
    ("cashflow_snapshot", "saving_inr"):        "saving",
    ("cashflow_snapshot", "sinking_inr"):       "sinking",
    ("cashflow_snapshot", "insurance_inr"):     "insurance",
    ("cashflow_snapshot", "leftover_inr"):      "pool",   # pool, NOT free
    # budget_status tool outputs
    ("budget_status", "net_spent_inr"):         "net_spent",
    ("budget_status", "total_budget_inr"):      "total_budget",
    ("budget_status", "over_by_inr"):           "over_by",
    # debt_snapshot tool outputs
    ("debt_snapshot", "total_balance_inr"):     "total_balance",
    ("debt_snapshot", "total_emi_inr"):         "total_emi",
    ("debt_snapshot", "foir_room_inr"):         "foir_room",
    # networth_snapshot
    ("networth_snapshot", "total_inr"):         "networth_total",
    # spend_by_category aggregate
    ("spend_by_category", "net_spent_inr"):     "net_spent",
}


# ── Module-level compute wrappers ────────────────────────────────────────────
# Defined at module scope so tests can monkeypatch them without touching the
# real compute functions.  Each does a local import to avoid import cycles
# (same pattern used by api/debt.py, api/budget.py, etc.).

def _compute_cashflow(db, household_id: int) -> dict:
    from api.cashflow import compute_cashflow
    return compute_cashflow(db, household_id)


def _compute_budget(db, household_id: int, mode: str) -> dict:
    from api.budget import compute_budget
    return compute_budget(mode=mode, db=db, household_id=household_id)


def _compute_debt(db, household_id: int) -> dict:
    from api.debt import compute_debt
    return compute_debt(db, household_id)


def _compute_assets(db, household_id: int) -> dict:
    from api.networth import compute_assets
    return compute_assets(db, household_id)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_inr(s: str) -> float:
    """Parse an Indian-format number string to float.
    '1,50,000' → 150000.0  |  '5,000.25' → 5000.25
    """
    return float(s.replace(",", ""))


def _within_tolerance(actual: float, expected: float,
                      abs_tol: float, rel_tol: float) -> bool:
    """True if |actual − expected| ≤ max(abs_tol, rel_tol × |expected|)."""
    tol = max(abs_tol, rel_tol * abs(expected))
    return abs(actual - expected) <= tol


def _infer_lens(tools_called: list[dict]) -> str:
    """Extract the `lens` arg from the first tool call that carries one.
    Defaults to 'cycle' (the canonical household lens).
    Note: T4 strips household_id from tool inputs — we don't look for it.
    """
    for tc in tools_called:
        inp = tc.get("input") or {}
        lens = inp.get("lens")
        if lens in ("cycle", "month"):
            return str(lens)
    return "cycle"


def _build_ground_truth(db: Any, household_id: int, lens: str) -> dict[str, Any]:
    """Re-derive every verifiable household figure from the live DB.

    Returns a dict with keys:
        canonical     {str: float}  label → float for every figure we can verify
        pool          float         monthly variable-spend pool (cashflow.leftover)
        actually_free float         pool − spent_this_period
        spent         float         gross spend this period from compute_budget
        total_budget  float         sum of all budget limits (0.0 if none set)
        is_over_budget bool         spent > total_budget and budget is configured

    All floats are 0.0 and canonical is {} when db is None or on compute failure.
    Failures are logged at WARNING and never propagate.
    """
    _empty: dict[str, Any] = {
        "canonical": {}, "pool": 0.0, "actually_free": 0.0,
        "spent": 0.0, "total_budget": 0.0, "is_over_budget": False,
    }
    if db is None:
        return _empty

    try:
        cash = _compute_cashflow(db, household_id)
        budget = _compute_budget(db, household_id, mode=lens)

        pool = float(cash.get("leftover") or 0.0)
        income = float(cash.get("income") or 0.0)
        emi = float(cash.get("emi") or 0.0)

        spent = float(budget.get("net_spent_inr") or 0.0)
        total_budget = float(
            sum(i.get("limit_inr", 0) for i in budget.get("items", []))
        )
        over_by = round(max(0.0, spent - total_budget), 2)
        actually_free = round(pool - spent, 2)

        canonical: dict[str, float] = {
            "income":        income,
            "emi":           emi,
            "fixed":         float(cash.get("fixed") or 0.0),
            "saving":        float(cash.get("saving") or 0.0),
            "sinking":       float(cash.get("sinking") or 0.0),
            "insurance":     float(cash.get("insurance") or 0.0),
            "pool":          pool,
            "spent":         spent,
            "net_spent":     spent,
            "actually_free": actually_free,
            "total_budget":  total_budget,
            "over_by":       over_by,
        }

        # Debt figures (optional — skip on any failure)
        try:
            debt = _compute_debt(db, household_id)
            canonical["total_balance"] = float(debt.get("total_balance") or 0.0)
            canonical["total_emi"] = float(debt.get("total_emi") or 0.0)
            # FOIR room: 50% of income − existing EMI (matches T3 debt_snapshot formula)
            canonical["foir_room"] = round(max(0.0, 0.50 * income - emi), 2)
        except Exception as exc:  # noqa: BLE001
            log.debug("number_check: debt lookup skipped: %s", exc)

        # Net worth (optional — skip on any failure)
        try:
            nw = _compute_assets(db, household_id)
            canonical["networth_total"] = float(nw.get("total_inr") or 0.0)
        except Exception as exc:  # noqa: BLE001
            log.debug("number_check: networth lookup skipped: %s", exc)

        return {
            "canonical":     canonical,
            "pool":          pool,
            "actually_free": actually_free,
            "spent":         spent,
            "total_budget":  total_budget,
            "is_over_budget": total_budget > 0 and spent > total_budget,
        }

    except Exception as exc:  # noqa: BLE001
        log.warning(
            "number_check: ground truth build failed (household=%s lens=%s): %s",
            household_id, lens, exc,
        )
        return _empty


# ── Violation checks ──────────────────────────────────────────────────────────

def _check_cited_numbers(
    cited_numbers: list[dict],
    canonical: dict[str, float],
    ok_tools: set[str],
    tools_called_present: bool,
) -> list[dict]:
    """Check every INR figure the model declared it used against a fresh DB recompute.

    cited_numbers entries with source='ground' (KB facts / definitions) are skipped —
    those are universal rules, not DB-derived values.
    Entries from failed tool calls (ok=False) are also skipped.
    """
    if not cited_numbers or not canonical:
        return []

    violations: list[dict] = []
    for cn in cited_numbers:
        if cn.get("unit") != "INR":
            continue
        if cn.get("source") == "ground":
            continue

        raw_value = cn.get("value")
        if raw_value is None:
            continue

        label = str(cn.get("label") or "")
        tool = str(cn.get("tool") or "")
        value = float(raw_value)

        # Skip numbers from tool calls recorded as failed
        if tools_called_present and tool and tool not in ok_tools:
            log.debug("number_check: skipping cited number from failed tool %s.%s=%s",
                      tool, label, value)
            continue

        expected = _resolve_expected(tool, label, canonical)
        if expected is None:
            log.debug("number_check: no canonical match for tool=%r label=%r", tool, label)
            continue

        if not _within_tolerance(value, expected, _CITED_ABS, _CITED_REL):
            violations.append({
                "kind":     "hallucinated_number",
                "figure":   value,
                "expected": expected,
                "detail": (
                    f"cited {label!r}=₹{value:,.2f} (via tool {tool!r}) "
                    f"but DB recomputes ₹{expected:,.2f} — "
                    f"diff ₹{abs(value - expected):,.2f}"
                ),
            })
    return violations


def _resolve_expected(tool: str, label: str, canonical: dict[str, float]) -> float | None:
    """Map a (tool, label) pair to a canonical float, or None if unknown."""
    key = _TOOL_FIELD_MAP.get((tool, label))
    if key is not None:
        return canonical.get(key)
    # Fuzzy fallback: strip common suffixes and try the label directly
    stripped = label.lower()
    for suffix in ("_inr", "_INR"):
        if stripped.endswith(suffix.lower()):
            stripped = stripped[: -len(suffix)]
            break
    return canonical.get(stripped) or canonical.get(label.lower())


def _check_gross_pool_as_free(
    answer_text: str,
    pool: float,
    actually_free: float,
) -> list[dict]:
    """Flag sentences that present the gross variable-spend pool as free/available cash.

    The ₹1,50,000 bug: pool=₹1,50,000, actually_free=₹5,000.
    BAD: "you have ₹1,50,000 free to spend" → violation.
    OK:  "pool is ₹1,50,000 but only ₹5,000 is free" → honest contrast, exempt.

    Exemption rule: if a sentence ALSO contains a figure matching actually_free, the
    sentence is presenting an honest contrast (pool vs what's free).  That is the
    correct framing — we must not penalise it.

    Only runs when pool and actually_free differ by more than the text tolerance.
    """
    # Guard: if pool ≈ actually_free (nothing spent yet) or pool is negative, skip
    if abs(pool - actually_free) <= _TEXT_ABS:
        return []
    if pool <= 0:
        return []

    violations: list[dict] = []
    seen_figures: set[float] = set()

    # Split on real sentence boundaries only: punctuation FOLLOWED BY whitespace, or newlines.
    # A bare "." must NOT split (it would tear "₹1.5 lakh" into "₹1" | "5 lakh" and orphan the
    # figure from its free-cash words — the exact reason the ₹1.7-lakh bug slipped through).
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer_text):
        sentence = sentence.strip()
        if not sentence or not _FREE_WORDS_RE.search(sentence):
            continue

        # Parse all ₹ figures in the sentence — shorthand-aware, so "₹1.5 lakh free" is caught
        # exactly like "₹1,50,000 free" (the ₹150k bug can't hide behind lakh/crore/k shorthand).
        figures: list[float] = _extract_rupee_figures(sentence)

        if not figures:
            continue

        # Exemption: if the sentence correctly cites actually_free alongside the pool,
        # it is contrasting pool vs free honestly ("pool ₹150k but only ₹5,000 is free")
        if any(_within_tolerance(f, actually_free, _TEXT_ABS, _TEXT_REL) for f in figures):
            continue

        # Flag figures that match the pool but not actually_free
        for figure in figures:
            if figure in seen_figures:
                continue
            pool_match = _within_tolerance(figure, pool, _TEXT_ABS, _TEXT_REL)
            af_match = _within_tolerance(figure, actually_free, _TEXT_ABS, _TEXT_REL)
            if pool_match and not af_match:
                seen_figures.add(figure)
                violations.append({
                    "kind":     "gross_pool_as_free",
                    "figure":   figure,
                    "expected": actually_free,
                    "detail": (
                        f"Answer presents pool ₹{pool:,.0f} as free/available cash, "
                        f"but actually_free = pool − spent = ₹{actually_free:,.0f}. "
                        f"Sentence: «{sentence[:200]}»"
                    ),
                })

    return violations


def _check_contradiction(answer_text: str) -> list[dict]:
    """Detect 'over budget' + 'plenty free' co-occurrence — a self-contradiction.

    Pure text — no DB needed.  If the prose simultaneously asserts "over budget"
    and "lots of available headroom", the answer is internally inconsistent:
    if the household has already exceeded the pool, there IS no meaningful headroom.
    """
    has_over = bool(_OVER_BUDGET_RE.search(answer_text))
    # "plenty free" = a magnitude word (plenty/lots/…) AND a free-cash word in the SAME answer
    has_plenty_free = (
        bool(_PLENTY_RE.search(answer_text)) and
        bool(_FREE_WORDS_RE.search(answer_text))
    )
    if has_over and has_plenty_free:
        return [{
            "kind":     "contradiction",
            "figure":   None,
            "expected": None,
            "detail": (
                "Answer simultaneously asserts 'over budget' and 'plenty of free/available cash'. "
                "These contradict each other: if the household has exceeded the variable-spend "
                "pool, there is no meaningful free headroom from that same pool.  Any purchase "
                "must be treated as a capital decision (borrow / liquidate / stagger)."
            ),
        }]
    return []


# ── Public API ────────────────────────────────────────────────────────────────

def check_numbers(
    answer_text: str,
    cited_numbers: list[dict] | None,
    tools_called: list[dict] | None,
    db: Any,
    household_id: int,
) -> dict[str, Any]:
    """Artha's unfair advantage: deterministic ₹-figure verification.  No LLM.

    Args:
        answer_text:    The advisor's final prose response (not tool scratch).
        cited_numbers:  List of {label, value, unit, tool, source, verified?} entries
                        assembled by T4 from the tool outputs the answer referenced.
                        May be None or [].  verified=null on input — T7 does not
                        mutate the list; violations.kind='hallucinated_number' is the
                        signal for T6 to mark verified=False on the offending entry.
        tools_called:   List of {name, input, ok} records in call order (T4 output).
                        Used to infer `lens` and to skip numbers from failed tools.
        db:             SQLAlchemy Session scoped to the caller's request.
                        Pass None to run only text-based checks (contradiction).
        household_id:   The tenant's household.id — every compute call is scoped here.

    Returns:
        {
            "violations": [
                {
                    "kind":     "hallucinated_number"
                              | "gross_pool_as_free"
                              | "contradiction",
                    "figure":   float | None,   # the figure as it appears in the answer
                    "expected": float | None,   # what the DB / formula says it should be
                    "detail":   str,            # human-readable explanation with context
                }
            ],
            "passed": bool,   # True iff violations is empty
        }

    Gate semantics (fail-closed):
        Any violation → passed=False.
        T6 runner maps passed=False on a P0 case to a gate failure that blocks go-live.
    """
    violations: list[dict] = []
    _cited = cited_numbers or []
    _called = tools_called or []

    if not answer_text:
        return {"violations": violations, "passed": True}

    # Step 1: infer lens from tool-call inputs (T4 logs lens per call; default 'cycle')
    lens = _infer_lens(_called)

    # Step 2: build ground truth from DB (empty dict if db is None)
    gt = _build_ground_truth(db, household_id, lens)
    canonical     = gt["canonical"]
    pool          = gt["pool"]
    actually_free = gt["actually_free"]

    # Step 3: cited_numbers → DB recompute check (hallucinated_number)
    ok_tools = {tc["name"] for tc in _called if tc.get("ok", True)}
    violations.extend(
        _check_cited_numbers(
            cited_numbers=_cited,
            canonical=canonical,
            ok_tools=ok_tools,
            tools_called_present=bool(_called),
        )
    )

    # Step 4: gross-pool-as-free scan (text + DB; skipped when db is None)
    if canonical:
        violations.extend(_check_gross_pool_as_free(answer_text, pool, actually_free))

    # Step 5: contradiction scan (pure text, always runs)
    violations.extend(_check_contradiction(answer_text))

    return {
        "violations": violations,
        "passed":     len(violations) == 0,
    }
