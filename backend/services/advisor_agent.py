"""Artha advisor agent — prompt→grounded-agent shift (T4).

Replaces the giant pre-stuffed text prompt in advisor.py with a SMALL system prompt
+ Anthropic tool-use loop.  The model CALLS deterministic household-scoped tools
(T3 TOOLS_SPEC/TOOL_IMPLS) and the knowledge-base (T5 ground) to fetch every number
and definition, instead of reading a pre-computed snapshot injected at call time.

Every turn records which tools were called and which numbers were cited, ready for:
  • T1 (advisor_store.record_turn) — persistence with cited_numbers + tools_called
  • T7 (number_check) — deterministic re-verification of every ₹ figure in the answer

Free-cash and hallucination bugs are now structural impossibilities:
  • actually_free is the ONLY source for any “can I spend” number.
  • emi_quote is the ONLY route to any EMI figure.
  • T7 recomputes every cited rupee from the DB and flags any mismatch.

FALLBACK FLAG: when settings.advisor_grounded is False (the default), reply() delegates
to the existing services.advisor.reply (prompt-based) so we can shadow-run the grounded
path before promoting it.  Set ADVISOR_GROUNDED=true in the environment to enable.

Multi-tenancy: household_id is injected from get_current_household into every tool
call by run_tools and is NEVER exposed in the model’s tool schema.
"""
from __future__ import annotations

import logging

log = logging.getLogger("artha.advisor_agent")


# ---------------------------------------------------------------------------
# Small system prompt — persona + reasoning rules + hard constraints
#
# Deliberately compact: the model fetches data via tools rather than reading a
# pre-stuffed context blob.  Every number it quotes must come from a tool call.
# (The old _SYSTEM in advisor.py is preserved as a fallback for the llm-unavailable
# path — do NOT delete it.)
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are Artha — the household’s personal CA and financial strategist. Reason like a top private
banker who knows their books. Figure out HOW to make things work; don’t default to refusal.

TOOLS — call them; never invent a number:
• actually_free(lens) → THE first call for ANY “can I buy / spend / afford” question.
  The gross pool (leftover) is NOT free — call this to get pool − already-spent.
• budget_status(lens) → budget vs actual, days left, over-budget flag
• spend_by_category(lens) → where money went this period
• emi_quote(principal, annual_pct, months) → the ONLY route to any EMI figure
• affordability(amount, lens) → spend vs capital routing; gives verdict + funding path
• debt_snapshot() → loan balances, rates, FOIR headroom
• networth_snapshot() → liquid assets by type
• cashflow_snapshot() → income → EMIs → fixed → savings → pool
• ground(query) → definitions and rules (FOIR, cycle vs month, actually_free formula…)

HARD RULES (never break):
1. NEVER quote a rupee figure you did not get from a tool. Call the tool first.
2. NEVER call leftover / pool “free”. Call actually_free() — pool minus already-spent.
3. NEVER do EMI arithmetic yourself. Call emi_quote().
4. NEVER say “over budget BUT plenty free” — those contradict each other.
   If actually_free is negative, say so plainly.
5. Missing info (car price not given) → state assumption: “assuming ₹8L…” then
   call emi_quote with that assumption.
6. The lens arrives as a prefix in the user message ([lens:cycle] or [lens:month]).
   Use that lens for all tool calls unless the question clearly implies the other.

HOW TO REASON:
• Spend or capital? amount > actually_free → capital (borrow / liquidate / stagger).
• For capital: FOIR headroom from debt_snapshot, liquidity from networth_snapshot,
  EMI from emi_quote. Give a concrete funding path.
• Prefer freeing money (close high-rate loan, redirect ending EMI) over saying no.

STYLE: text a smart friend who trusts you. Short for quick questions; full walk-through
for planning. Cite actual numbers from tool calls. No corporate hedging.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reply(
    db,
    messages: list[dict],
    household_id: int | None = None,
    lens: str = "cycle",
    chat_id: int | None = None,
) -> dict:
    """Run the tool-use advisor loop and return a grounded answer.

    When settings.advisor_grounded is False (default), delegates to the legacy
    services.advisor.reply (prompt-based) so shadow-running is possible before
    promoting the grounded path.  Set ADVISOR_GROUNDED=true in the environment
    to enable the full tool-calling path.

    Args:
        db:           SQLAlchemy session (scoped to the HTTP request).
        messages:     Conversation history [{role, content}, ...].
                      Usually from the frontend (last 10 used).
        household_id: Tenant key — injected from get_current_household, never from the
                      client.
        lens:         Active budget lens ('cycle'|'month') matching the UI's PeriodToggle.
        chat_id:      Existing AdvisorChat.id to continue, or None to start a new one.
                      Only used in grounded mode (legacy path does not persist).

    Returns:
        {
          text:                 str | None  — final answer, None on LLM failure
          cited_numbers:        list[dict]  — ₹ figures mentioned in the answer, for T1/T7
          tools_called:         list[dict]  — {name, input, ok} only (no output), for T1
          model:                str
          chat_id:              int | None  — created or continued session
          user_message_id:      int | None
          assistant_message_id: int | None
        }
    """
    from config import settings
    from services import llm

    _model = settings.llm_model_smart
    _empty = {
        "text": None,
        "cited_numbers": [],
        "tools_called": [],
        "model": _model,
        "chat_id": chat_id,
        "user_message_id": None,
        "assistant_message_id": None,
    }

    # ── Fallback path: prompt-based advisor (shadow-run guard) ──────────────────────
    # Default is False so existing behaviour is preserved until the grounded path
    # passes P0 evals on real traffic.  Flip ADVISOR_GROUNDED=true to enable.
    if not getattr(settings, "advisor_grounded", False):
        try:
            from services.advisor import reply as _legacy_reply
            # Legacy signature: reply(db, messages, household_id, period) -> str
            text = _legacy_reply(db, messages, household_id=household_id, period=lens)
            if not isinstance(text, str):
                # Guard against future return-type changes in advisor.py
                text = str(text) if text is not None else None
            return {**_empty, "text": text}
        except Exception as exc:  # noqa: BLE001
            log.warning("advisor_agent: legacy advisor.reply failed, returning None: %s", exc)
            return _empty

    # ── Grounded path: tool-use loop ───────────────────────────────────────────────
    from services.advisor_tools import TOOL_IMPLS, TOOLS_SPEC

    if not llm.available():
        log.debug("advisor_agent.reply: LLM unavailable (no API key)")
        return _empty

    anthro_messages = _build_messages(messages, lens)
    # Extract the raw user message BEFORE _build_messages injects the [lens:X] prefix,
    # so the persisted content matches what the user actually typed.
    user_msg = _extract_user_message(messages)

    result = llm.run_tools(
        anthro_messages,
        system=_SYSTEM,
        tools=TOOLS_SPEC,
        tool_impls=TOOL_IMPLS,
        db=db,
        household_id=household_id,
        model=_model,
        max_rounds=6,
        timeout=60.0,
    )

    final_text = result.get("final_text")

    # Strip 'output' from tool_calls before persistence.
    # The data-model contract for advisor_message.tools_called is {name, input, ok} only.
    # The full version (including output) was used inside run_tools() for _collect_numbers;
    # T7 recomputes from the DB and doesn't need the stored output.
    tools_called_persisted = [
        {"name": t["name"], "input": t["input"], "ok": t["ok"]}
        for t in result.get("tool_calls", [])
    ]

    # Persist the turn — only when we have an answer and a scoped household.
    persist_ids: dict = {}
    if final_text and household_id is not None:
        try:
            from services.advisor_store import record_turn
            persist_ids = record_turn(
                db=db,
                household_id=household_id,
                user_msg=user_msg,
                assistant_msg=final_text,
                lens=lens,
                cited_numbers=result.get("numbers_cited", []),
                tools_called=tools_called_persisted,
                model=result.get("model", _model),
                chat_id=chat_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("advisor_agent: record_turn failed (non-fatal): %s", exc)

    return {
        "text": final_text,
        "cited_numbers": result.get("numbers_cited", []),
        "tools_called": tools_called_persisted,
        "model": result.get("model", _model),
        "chat_id": persist_ids.get("chat_id", chat_id),
        "user_message_id": persist_ids.get("user_message_id"),
        "assistant_message_id": persist_ids.get("assistant_message_id"),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_user_message(messages: list[dict]) -> str:
    """Last non-empty user message from the raw conversation history.

    Used for record_turn persistence — extracted BEFORE _build_messages injects
    the [lens:X] prefix so the stored content matches what the user typed.
    """
    for m in reversed(messages or []):
        if m.get("role") == "user":
            content = (m.get("content") or "").strip()
            if content:
                return content
    return ""


def _build_messages(messages: list[dict], lens: str) -> list[dict]:
    """Convert frontend {role, content} list to Anthropic API format.

    Injects the active lens as a [lens:X] prefix on the last user message so the
    model always knows which period to pass when calling tools.  Normalises roles,
    filters empties, merges consecutive same-role messages (Anthropic requires strict
    alternation), and ensures the conversation starts and ends with a user message.
    """
    if not messages:
        return [{"role": "user", "content": f"[lens:{lens}] Hello, can you help me?"}]

    # Normalise and filter
    norm: list[dict] = []
    for m in messages[-10:]:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        role = role if role in ("user", "assistant") else "user"
        norm.append({"role": role, "content": content})

    if not norm:
        return [{"role": "user", "content": f"[lens:{lens}] Hello"}]

    # Merge consecutive same-role messages (avoids Anthropic API validation error)
    merged: list[dict] = []
    for m in norm:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1] = {
                "role": m["role"],
                "content": merged[-1]["content"] + "\n" + m["content"],
            }
        else:
            merged.append(dict(m))

    # Must start with a user message
    if merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": f"[lens:{lens}] (context)"})

    # Inject lens into the final user message (or append one)
    if merged[-1]["role"] == "user":
        merged[-1] = {
            "role": "user",
            "content": f"[lens:{lens}] " + merged[-1]["content"],
        }
    else:
        merged.append({"role": "user", "content": f"[lens:{lens}] (please respond)"})

    return merged
