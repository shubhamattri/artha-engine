"""\
LLM-Judge for Artha advisor eval pipeline (T8 — EM·Eval&Quality).

Qualitative scoring of advisor responses on three dimensions that the deterministic
T7 number-verifier cannot evaluate:

  faithfulness          (0.0–1.0)  — did the advisor ground all reasoning in
                                     tool-derived numbers and apply the free-cash
                                     rule correctly?  Invented figures, presenting
                                     the gross pool as free, or 'over-budget AND
                                     plenty-free' contradictions all reduce this.

  not_too_conservative  (bool)     — did the advisor give CONCRETE, actionable
                                     advice?  "That's risky" alone, or an outright
                                     refusal when a viable path exists, scores False.

  asks_for_missing_inputs (bool)   — when a key input was absent (e.g. the car
                                     price), did the advisor ask for it OR explicitly
                                     state its assumption?  Silently inventing a
                                     number scores False.

This module is called by the T6 runner after T7 number_check for each eval case.
Uses settings.llm_model_smart (the reasoning tier).

Fail-closed contract:
  • Any API error, parse failure, OR prompt-assembly exception → is_error=True,
    pass=False, score=0.0.  A crash is not fail-closed.
  • A missing metric is never silently treated as a pass.

Prompt-safety note (V2):
  The prompt is built using str.replace() on fixed sentinels, NEVER str.format().
  This prevents KeyError/ValueError when user questions or advisor answers contain
  literal braces (JSON snippets, code blocks, currency with brace formatting).
  The fix is especially important for the T9 coach path, which calls the judge over
  STORED production user questions + advisor answers that can contain arbitrary text.
"""

from __future__ import annotations

import json
import logging
import re

from config import settings

log = logging.getLogger("artha.evals.judge")

# Increment when prompt changes would invalidate historical comparisons.
JUDGE_PROMPT_VERSION = "V2"

FAITHFULNESS_PASS_THRESHOLD = 0.80

_W_FAITHFULNESS = 0.60
_W_CONSERVATIVE = 0.25
_W_MISSING_INPUTS = 0.15


_SYSTEM = (
    "You are an expert evaluator for Artha, an AI household-finance advisor. "
    "Grade responses objectively against the rules below. "
    "Return ONLY valid JSON — absolutely no other text before or after it."
)

# ── Prompt template — SENTINEL-BASED (never use str.format on this) ───────────
#
# Sentinel tokens are replaced by _build_prompt() using str.replace().  We use
# placeholder strings that cannot appear in any advisor question or answer:
#
#   __QUESTION__         ← the user's question
#   __TOOL_CALLS__       ← formatted tool-call summary
#   __ANSWER__           ← the advisor's final prose response
#   __RUBRIC_SECTION__   ← optional per-case criteria block (replaced with "" if none)
#
# The JSON example in the grading instructions uses bare { } (valid because we
# never call str.format on this template).
_PROMPT_TEMPLATE = """\
## Artha Advisor Evaluation

You are grading an AI household-finance advisor response on 3 dimensions.

### The Advisor's Non-Negotiable Rules
1. FREE-CASH RULE: The variable-spend pool (leftover = income − EMIs − fixed − savings) \
is NOT spare cash — it is the budget from which ALL everyday spending is drawn. \
The only valid "free" figure is: actually_free = pool − already_spent_this_period. \
Presenting the gross pool as "free" is always wrong.
2. NO CONTRADICTION: Cannot simultaneously say "you're over budget" AND "you have plenty \
of free money / room to spend". These contradict each other: if over budget, actually_free \
is zero or negative.
3. NO INVENTED NUMBERS: Every balance, rate, EMI, and spend figure must come from a tool \
call. If a key input is missing (e.g. the purchase price), the advisor must either ASK for \
it explicitly or state its assumption with a label like "assuming a ₹8L car…".
4. EMI DISCIPLINE: EMI figures must come from the emi_quote tool (never self-computed).
5. ACTION BIAS: Default to HOW-TO-make-it-work. A capital decision (borrow / liquidate / \
stagger) is a valid answer for amounts above the actually_free threshold. \
"That's risky" with no path forward is not an acceptable response.

### User Question
__QUESTION__

### Tools Called (in order)
__TOOL_CALLS__

### Advisor Response
__ANSWER__
__RUBRIC_SECTION__
---

### Grading Instructions

**faithfulness** (float 0.0–1.0):
- 1.0 = correctly cited actually_free (not the gross pool) as the free-to-spend amount;
  all ₹ figures match tool outputs; no over-budget + plenty-free contradiction.
- 0.5 = mostly correct but minor framing slip (e.g. said "pool" instead of "actually free"
  but gave the right figure; or used one unrelated figure without sourcing it).
- 0.0 = used the gross pool as the free amount; OR invented a balance/rate/EMI; OR said
  "over budget" AND "plenty free" in the same response; OR EMI computed outside emi_quote.

**not_too_conservative** (bool):
- true  = gave a CONCRETE recommendation: a go/hold verdict with specific numbers, OR a
          funding path (close loan X to free ₹Y EMI, or sell asset Z for down payment, or
          stagger over two cycles), OR an honest "hold" with the exact shortfall named.
- false = only refused or warned ("you shouldn't do this", "consult a professional") with
          no actionable path; OR asked 3+ clarifying questions before providing any guidance.

**asks_for_missing_inputs** (bool):
- true  = (a) no key inputs were missing for the question, OR
           (b) a key input WAS missing and the advisor explicitly ASKED ("what's your budget
               for the car?"), OR
           (c) a key input was missing and advisor stated assumption ("assuming a ₹8L car…").
- false = a key input was missing and the advisor silently used a specific figure without
          labeling it as an assumption.

Return ONLY valid JSON (no markdown fences, no preamble):
{
  "faithfulness": <float 0.0-1.0>,
  "not_too_conservative": <bool>,
  "asks_for_missing_inputs": <bool>,
  "reason": "<1–2 sentences: what the response did right or wrong>"
}
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _coerce_bool(raw: object, *, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        lower = raw.strip().lower()
        if lower in {"true", "yes", "1"}:
            return True
        if lower in {"false", "no", "0"}:
            return False
    return default


def _summarize_tool_calls(tool_calls: list[dict]) -> str:
    if not tool_calls:
        return "  (no tools called — advisor answered from training data or snapshot)"
    parts: list[str] = []
    for tc in tool_calls:
        name = tc.get("name", "?")
        ok_mark = "✓" if tc.get("ok", True) else "✗"
        raw_input = {k: v for k, v in tc.get("input", {}).items() if k != "household_id"}
        parts.append(f"  {ok_mark} {name}({raw_input})")
    return "\n".join(parts)


def _parse_raw(raw: str) -> dict | None:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _build_error_result(reason: str) -> dict:
    return {
        "pass": False,
        "score": 0.0,
        "reason": reason,
        "faithfulness": 0.0,
        "not_too_conservative": False,
        "asks_for_missing_inputs": False,
        "is_error": True,
        "prompt_version": JUDGE_PROMPT_VERSION,
    }


def _compute_from_sub_scores(
    faithfulness: float,
    not_too_conservative: bool,
    asks_for_missing_inputs: bool,
    reason: str,
) -> dict:
    score = (
        faithfulness * _W_FAITHFULNESS
        + float(not_too_conservative) * _W_CONSERVATIVE
        + float(asks_for_missing_inputs) * _W_MISSING_INPUTS
    )
    passed = (
        faithfulness >= FAITHFULNESS_PASS_THRESHOLD
        and not_too_conservative
        and asks_for_missing_inputs
    )
    return {
        "pass": passed,
        "score": round(score, 4),
        "reason": reason,
        "faithfulness": round(faithfulness, 4),
        "not_too_conservative": not_too_conservative,
        "asks_for_missing_inputs": asks_for_missing_inputs,
        "is_error": False,
        "prompt_version": JUDGE_PROMPT_VERSION,
    }


def _build_prompt(
    question: str,
    tool_summary: str,
    answer: str,
    rubric: list[str] | None,
) -> str:
    """Build the judge prompt using brace-safe sentinel replacement.

    NEVER call str.format() on user-supplied content: any literal { or } in
    question/answer/tool_summary would raise KeyError/ValueError.  str.replace()
    on fixed sentinel strings is immune to that class of crash.

    Args:
        question:     The user's question, verbatim.
        tool_summary: Pre-formatted tool-call list from _summarize_tool_calls().
        answer:       The advisor's final prose response.
        rubric:       Optional list of case-specific criteria descriptions (plain
                      English sentences).  Each is injected into the prompt as a
                      numbered criterion so the judge grades the response against
                      those specifics in addition to the standard dimensions.
                      Influences faithfulness / not_too_conservative /
                      asks_for_missing_inputs holistically — the judge is not
                      required to return per-criterion booleans (that is a future
                      T6 runner enhancement).
    """
    rubric_section = ""
    if rubric:
        lines: list[str] = [
            "\n### Case-Specific Criteria",
            (
                "In addition to the standard rules above, evaluate the response against "
                "these specific criteria and incorporate them into your holistic scores:"
            ),
        ]
        for i, criterion in enumerate(rubric, 1):
            lines.append(f"  {i}. {criterion}")
        rubric_section = "\n" + "\n".join(lines) + "\n"

    return (
        _PROMPT_TEMPLATE
        .replace("__QUESTION__", question)
        .replace("__TOOL_CALLS__", tool_summary)
        .replace("__ANSWER__", answer)
        .replace("__RUBRIC_SECTION__", rubric_section)
    )


# ── Public API ────────────────────────────────────────────────────────────────

def score(
    question: str,
    answer: str,
    tool_calls: list[dict],
    *,
    rubric: list[str] | None = None,
    model: str | None = None,
    timeout: float = 60.0,
) -> dict:
    """
    Qualitative judge scoring for one advisor turn.

    Arguments
    ---------
    question    : the user's message that was sent to the advisor
    answer      : the advisor's final text response (not tool scratch)
    tool_calls  : list[{name, input, ok}] from advisor_agent RunResult.tool_calls;
                  household_id is stripped before passing to the prompt
    rubric      : optional list of case-specific criteria descriptions to inject into
                  the judge prompt for more targeted grading.  Each element is a plain-
                  English sentence describing what the advisor's response must do or
                  avoid (e.g. "Answer must name the billing-cycle lens explicitly").
                  Criteria influence the holistic faithfulness / not_too_conservative /
                  asks_for_missing_inputs scores.  T6 can pass case assertion descriptions
                  here to make the judge more precise for specific P0 cases.
                  Default None means standard holistic grading only.

    Returns a dict (superset of the contract's {pass, score, reason}):
    {
        "pass":                  bool,
        "score":                 float,   # 0.0–1.0
        "reason":                str,
        "faithfulness":          float,   # 0.0–1.0
        "not_too_conservative":  bool,
        "asks_for_missing_inputs": bool,
        "is_error":              bool,    # True when API/parse/assembly failed
        "prompt_version":        str,
    }

    Fail-closed: any exception anywhere in this function (including prompt assembly,
    which runs on untrusted user text) → pass=False, score=0.0, is_error=True.
    The T6 gate therefore never silently treats a broken run as passing.
    """
    from services import llm  # noqa: PLC0415 — local import avoids cycle at load

    if not llm.available():
        return _build_error_result(
            "Judge LLM unavailable (no ANTHROPIC_API_KEY) — mark for human review"
        )

    if not answer or not answer.strip():
        return _build_error_result("Empty advisor response — nothing to judge")

    # ── Prompt assembly — wrapped in fail-closed try/except ───────────────────
    # The prompt inserts user-supplied text (question, answer) that may contain
    # literal braces, Unicode, or any other character.  _build_prompt() uses
    # str.replace() on sentinels so braces can never cause a crash.  The outer
    # try/except catches any remaining unexpected failure (encoding errors, OOM,
    # etc.) and converts them to a fail-closed error result rather than an
    # uncaught exception propagating to the T6 runner.
    try:
        tool_summary = _summarize_tool_calls(tool_calls or [])
        prompt = _build_prompt(question, tool_summary, answer, rubric)
    except Exception as exc:  # noqa: BLE001
        log.warning("judge.score: prompt assembly failed: %s", exc)
        return _build_error_result(f"Prompt assembly failed: {exc}")

    raw = llm.complete(
        prompt,
        system=_SYSTEM,
        model=model or settings.llm_model_smart,
        max_tokens=512,
        timeout=timeout,
    )

    if not raw:
        return _build_error_result("LLM call returned None — API failure or timeout")

    data = _parse_raw(raw)
    if data is None:
        log.warning("judge.score: could not parse LLM response as JSON. raw=%r", raw[:300])
        return _build_error_result(
            f"LLM response not parseable as JSON (prompt_version={JUDGE_PROMPT_VERSION})"
        )

    try:
        faithfulness = _clamp(float(data.get("faithfulness", 0.0)))
        not_conservative = _coerce_bool(data.get("not_too_conservative"), default=False)
        asks_inputs = _coerce_bool(data.get("asks_for_missing_inputs"), default=False)
        reason = str(data.get("reason", "")).strip() or "No reason provided"
    except (TypeError, ValueError) as exc:
        log.warning("judge.score: sub-score extraction failed: %s, data=%r", exc, data)
        return _build_error_result(f"Sub-score extraction failed: {exc}")

    return _compute_from_sub_scores(faithfulness, not_conservative, asks_inputs, reason)
