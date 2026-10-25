"""Grounded-advisor tools (T3) + agent (T4) — Slice 2.

All figures here are SYNTHETIC fixtures — never the real household's numbers. The one invariant
that matters: actually_free = pool - spent (the ₹150k fix), and household_id is never a
model-visible tool argument (the tenancy guarantee).
"""

from __future__ import annotations

import api.budget as budget_mod
import api.cashflow as cashflow_mod
from services import advisor_tools


def _patch(monkeypatch, pool: float, spent: float):
    """Inject a synthetic cashflow pool + budget spend into the tool impls."""
    monkeypatch.setattr(cashflow_mod, "compute_cashflow",
                        lambda db, hh: {"income": 200000.0, "emi": 40000.0, "fixed": 20000.0,
                                        "saving": 10000.0, "sinking": 5000.0, "insurance": 2000.0,
                                        "leftover": pool})
    monkeypatch.setattr(budget_mod, "compute_budget",
                        lambda month=None, mode="cycle", db=None, household_id=None, anchor=None: {
                            "net_spent_inr": spent,
                            "items": [{"category": "Food", "limit_inr": 20000.0, "spent_inr": spent}],
                            "cycle": {"days_total": 30, "days_elapsed": 22, "days_left": 8},
                            "period_start": "2026-07-12", "period_end": "2026-08-11"})


def test_actually_free_is_pool_minus_spent(monkeypatch):
    """THE ₹150k fix: the tool must return pool - spent, never the gross pool."""
    _patch(monkeypatch, pool=100_000.0, spent=90_000.0)
    out = advisor_tools.TOOL_IMPLS["actually_free"](object(), 1, lens="cycle")
    assert out["pool_inr"] == 100_000.0
    assert out["spent_this_period_inr"] == 90_000.0
    assert out["actually_free_inr"] == 10_000.0     # pool - spent, NOT the pool
    assert out["over_budget"] is False


def test_actually_free_flags_over_budget(monkeypatch):
    """When spend exceeds the pool, actually_free is negative and over_budget is True."""
    _patch(monkeypatch, pool=100_000.0, spent=120_000.0)
    out = advisor_tools.TOOL_IMPLS["actually_free"](object(), 1, lens="cycle")
    assert out["actually_free_inr"] == -20_000.0
    assert out["over_budget"] is True


def test_household_id_never_in_tool_schema():
    """Tenancy guarantee: the model can never set household_id — it's injected server-side."""
    for spec in advisor_tools.TOOLS_SPEC:
        props = spec.get("input_schema", {}).get("properties", {})
        assert "household_id" not in props, f"{spec['name']} leaks household_id into the schema"


def test_emi_quote_matches_reducing_balance(monkeypatch):
    """emi_quote is the ONLY EMI source — it must equal the deterministic reducing-balance formula."""
    from services.advisor import _emi
    out = advisor_tools.TOOL_IMPLS["emi_quote"](object(), 1, principal=800000.0, annual_pct=9.0, months=60)
    expected = round(_emi(800000.0, 9.0, 60), 0)
    assert abs(out["emi_inr"] - expected) <= 1.0
    assert out["emi_inr"] * 60 > out["principal_inr"]   # sanity: total paid > principal


def test_grounded_flag_off_delegates_to_legacy(monkeypatch):
    """advisor_grounded=False (default) → the agent returns the legacy answer, persists nothing."""
    from config import settings
    monkeypatch.setattr(settings, "advisor_grounded", False, raising=False)
    import services.advisor as legacy
    monkeypatch.setattr(legacy, "reply", lambda db, msgs, household_id, period: "LEGACY-ANSWER")
    from services.advisor_agent import reply
    out = reply(object(), [{"role": "user", "content": "hi"}], household_id=1, lens="cycle")
    assert out["text"] == "LEGACY-ANSWER"
    assert out["assistant_message_id"] is None          # legacy path does not persist
