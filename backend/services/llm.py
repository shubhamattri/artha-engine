"""Shared LLM client (Claude) — the AI intelligence layer.

Deliberately thin + SAFE: if no ANTHROPIC_API_KEY is set (or the SDK/network fails), every helper returns
None and callers fall back to the deterministic/rules path — the app keeps working exactly as before.
The LLM never computes financial numbers; it categorises, explains, and converses over numbers the
deterministic engine already produced.
"""

from __future__ import annotations

import json
import logging

from config import settings

log = logging.getLogger("artha.llm")


def available() -> bool:
    """True when an API key is configured — gate AI paths on this."""
    return bool(settings.anthropic_api_key)


def _client(timeout: float = 18.0):
    import anthropic  # lazy import so the app runs without the package installed

    # HARD timeout + single retry: an insight/chat request must NEVER hang the HTTP handler. If the
    # model is slow or the network stalls, the call raises fast and the caller falls back to the
    # deterministic path — the screen resolves to real content instead of a stuck "Reading…" spinner.
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=timeout, max_retries=1)


def complete(prompt: str, *, system: str | None = None, model: str | None = None,
             max_tokens: int = 1024, timeout: float = 18.0) -> str | None:
    """One-shot completion. Returns the text, or None on any failure (caller falls back)."""
    if not available():
        return None
    try:
        client = _client(timeout=timeout)
        msg = client.messages.create(
            model=model or settings.llm_model_fast,
            max_tokens=max_tokens,
            system=system or "You are Artha, a precise, trustworthy household-finance assistant.",
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    except Exception as e:  # noqa: BLE001 — never let an AI failure break a request
        log.warning("LLM call failed, falling back: %s", e)
        return None


def complete_json(prompt: str, *, system: str | None = None, model: str | None = None,
                  max_tokens: int = 2048):
    """Completion expected to return JSON. Returns the parsed object, or None on failure."""
    raw = complete(prompt, system=system, model=model, max_tokens=max_tokens)
    if not raw:
        return None
    try:
        s = raw.strip()
        if s.startswith("```"):  # strip ```json fences if the model adds them
            s = s.split("```", 2)[1].lstrip("json").strip() if "```" in s[3:] else s.strip("`")
        return json.loads(s)
    except Exception as e:  # noqa: BLE001
        log.warning("LLM JSON parse failed: %s", e)
        return None


# ── Advisor tool-use loop (grounded advisor — T4) ──────────────────────────────
# The grounded advisor calls deterministic tools instead of reading a pre-stuffed
# prompt. run_tools drives the Anthropic tool-use loop; _collect_numbers records the
# ₹ figures the answer actually cited (so T7 can re-verify each against the DB).

# tool-output field -> (human label, unit). Only these are treated as cited numbers.
_NUMERIC_FIELDS: dict[str, tuple[str, str]] = {
    "actually_free_inr": ("Actually free to spend", "INR"),
    "pool_inr": ("Variable spend pool", "INR"),
    "spent_this_period_inr": ("Spent this period", "INR"),
    "net_spent_inr": ("Net spent this period", "INR"),
    "total_budget_inr": ("Total budget", "INR"),
    "over_by_inr": ("Over budget by", "INR"),
    "principal_inr": ("Principal", "INR"),
    "emi_inr": ("Monthly EMI", "INR"),
    "total_payment_inr": ("Total payment", "INR"),
    "total_interest_inr": ("Total interest", "INR"),
    "annual_pct": ("Annual interest rate", "pct"),
    "amount_inr": ("Purchase amount", "INR"),
    "remaining_after_inr": ("Remaining after purchase", "INR"),
    "max_loan_inr": ("Max eligible loan", "INR"),
    "down_payment_inr": ("Down payment needed", "INR"),
    "shortfall_inr": ("Funding shortfall", "INR"),
    "new_emi_inr": ("New EMI", "INR"),
    "foir_pct": ("FOIR percentage", "pct"),
    "total_balance_inr": ("Total loan balance", "INR"),
    "total_emi_inr": ("Total loan EMIs", "INR"),
    "foir_room_inr": ("FOIR headroom", "INR"),
    "total_inr": ("Total net assets", "INR"),
    "income_inr": ("Monthly income", "INR"),
    "fixed_inr": ("Fixed expenses", "INR"),
    "saving_inr": ("Monthly savings", "INR"),
    "sinking_inr": ("Sinking fund set-aside", "INR"),
    "insurance_inr": ("Insurance premium", "INR"),
    "leftover_inr": ("Variable spend pool (leftover)", "INR"),
    "months": ("Loan tenure", "months"),
}


def _figures_in_text(text: str) -> list[float]:
    """All ₹ amounts in text as absolute rupees (lakh/crore/k expanded). Delegates to the
    number-verifier's extractor so the citation filter and T7 use ONE normalisation."""
    try:
        from evals.number_check import _extract_rupee_figures
        return _extract_rupee_figures(text)
    except Exception:  # noqa: BLE001 — verifier optional; degrade to no-filter
        return []


def _value_in_text(value: float, text_figures: list[float]) -> bool:
    """True if `value` appears in `text_figures` within display-rounding tolerance
    (₹200 abs or 2% rel — covers '₹1.5L' ≈ ₹1,50,000)."""
    for fig in text_figures:
        if abs(value - fig) <= max(200.0, 0.02 * abs(value)):
            return True
    return False


def _collect_numbers(tool_calls: list[dict], answer_text: str | None = None) -> list[dict]:
    """The ₹ figures the answer cited, drawn from successful tool outputs, de-duped by
    (tool, field). When answer_text is given, INR values are filtered to those that actually
    appear in the prose (so all 8 cashflow fields aren't marked 'cited' when 2 were used).
    T7 re-verifies each against a fresh DB recompute."""
    raw: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for tc in tool_calls:
        if not tc.get("ok"):
            continue
        name = tc.get("name", "")
        out = tc.get("output") or {}
        source = "ground" if name == "ground" else "tool"
        for field, (label, unit) in _NUMERIC_FIELDS.items():
            if field not in out or not isinstance(out[field], (int, float)):
                continue
            key = (name, field)
            if key in seen:
                continue
            seen.add(key)
            raw.append({"label": label, "value": float(out[field]), "unit": unit,
                        "tool": name, "source": source, "verified": None})
    if not answer_text or not raw:
        return raw
    figs = _figures_in_text(answer_text)
    return [r for r in raw if r["unit"] != "INR" or _value_in_text(r["value"], figs)]


def run_tools(messages: list[dict], *, system: str, tools: list[dict], tool_impls: dict,
              db, household_id, model: str | None = None, max_rounds: int = 6,
              timeout: float = 60.0) -> dict:
    """Anthropic tool-use loop for the grounded advisor. Injects household_id server-side into
    every tool call (NEVER from the model — the tenancy guarantee), loops until end_turn or
    max_rounds. Like complete(), any failure returns {final_text: None, ...} so callers degrade.
    Returns {final_text, tool_calls:[{name,input,output,ok}], numbers_cited:[...], model}."""
    _model = model or settings.llm_model_smart
    _empty = {"final_text": None, "tool_calls": [], "numbers_cited": [], "model": _model}
    if not available():
        return _empty
    tool_calls: list[dict] = []
    try:
        client = _client(timeout=timeout)
        current = list(messages)
        for _round in range(max_rounds):
            resp = client.messages.create(model=_model, max_tokens=2000, system=system,
                                          tools=tools, messages=current)
            if resp.stop_reason in ("end_turn", "stop_sequence", "max_tokens"):
                final = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
                return {"final_text": final or None, "tool_calls": tool_calls,
                        "numbers_cited": _collect_numbers(tool_calls, final or ""), "model": _model}
            if resp.stop_reason != "tool_use":
                log.warning("run_tools unexpected stop_reason=%r", resp.stop_reason)
                final = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
                return {"final_text": final or None, "tool_calls": tool_calls,
                        "numbers_cited": _collect_numbers(tool_calls, final or ""), "model": _model}
            current.append({"role": "assistant", "content": resp.content})
            results: list[dict] = []
            for block in resp.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                name = block.name
                safe_input = {k: v for k, v in dict(block.input).items() if k != "household_id"}
                impl = tool_impls.get(name)
                if impl is None:
                    err = {"error": f"Unknown tool: {name}"}
                    tool_calls.append({"name": name, "input": safe_input, "output": err, "ok": False})
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": json.dumps(err), "is_error": True})
                    continue
                try:
                    out = impl(db, household_id, **safe_input)
                    ok = True
                except Exception as tool_err:  # noqa: BLE001
                    log.warning("Tool %r failed: %s", name, tool_err)
                    out = {"error": str(tool_err)}
                    ok = False
                tool_calls.append({"name": name, "input": safe_input, "output": out, "ok": ok})
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out, default=str), **({} if ok else {"is_error": True})})
            if results:
                current.append({"role": "user", "content": results})
            else:
                log.warning("run_tools: tool_use stop but no tool_use blocks")
                break
        log.warning("run_tools hit max_rounds=%d without end_turn", max_rounds)
        return {"final_text": None, "tool_calls": tool_calls,
                "numbers_cited": _collect_numbers(tool_calls), "model": _model}
    except Exception as e:  # noqa: BLE001 — never let the tool loop break the request
        log.warning("run_tools failed: %s", e)
        return {"final_text": None, "tool_calls": tool_calls,
                "numbers_cited": _collect_numbers(tool_calls), "model": _model}
